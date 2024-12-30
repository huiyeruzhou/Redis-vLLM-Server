import time
import json
from omegaconf import DictConfig, OmegaConf

from redis_util.redis_middleware import RedisMiddleWare
from model import VLLMProtocol, VLLMModel

# 定义 TreeNode 类
class TreeNode:
    def __init__(self, value: str, parent: 'TreeNode' = None):
        self.value = value
        self.parent = parent
        self.children = []
        self.trajectory = self._generate_trajectory()
        self.score = 0.0

    def add_child(self, child: 'TreeNode'):
        self.children.append(child)

    def _generate_trajectory(self) -> str:
        nodes = []
        current = self
        while current:
            nodes.append(current.value)
            current = current.parent
        return '\n\n'.join(reversed(nodes))

    def __repr__(self):
        return f"TreeNode(value={self.value}, score={self.score})"
    
    def to_dict(self):
        return {
            'value': self.value,
            'score': self.score,
            'children': [child.to_dict() for child in self.children]
        }


# 定义树搜索函数
from collections import Counter
def tree_search(middleware: RedisMiddleWare, root_node: TreeNode, depth: int, evaluate_fn):
    current_level = [root_node]
    token_counter = Counter()
    for d in range(depth - 1):
        trajectories = [node.trajectory for node in current_level]
        results: list[VLLMProtocol] = middleware.process_requests(trajectories)
        next_level_nodes = []
        for node, result in zip(current_level, results):
            token_counter['input_tokens'] += result.input_tokens
            token_counter['output_tokens'] += result.output_tokens
            for c in result.texts:
                new_child = TreeNode(c, node)
                node.add_child(new_child)
                value_score = evaluate_fn(middleware, new_child.trajectory)
                new_child.score = value_score
                next_level_nodes.append(new_child)
            

        # 根据估值进行排序，选择 top 5 作为下一层的起始节点
        next_level_nodes.sort(key=lambda x: x.score, reverse=True)
        current_level = [node for node in next_level_nodes[:5]]

    # print(f"Final Trajectory: {current_level[0].trajectory}")
    return root_node, current_level[0], token_counter

# 定义评估函数
def evaluate_fn(middleware: RedisMiddleWare, trajectory: str) -> float:
    return len(trajectory.split('->'))

def client_request(args: tuple[int, TreeNode]):
    global config
    client_id, root_node = args
    middleware = RedisMiddleWare(config=config, worker=config.inference_server.worker, model_cls=VLLMModel, name=f"Client{client_id:02d}")
    
    import signal
    signal.signal(signal.SIGINT, middleware.exit_handler)

    try:
        start_time = time.time()
        root, chosen, token_counter = tree_search(middleware, root_node, depth=5, evaluate_fn=evaluate_fn)
        end_time = time.time()
        time_elapsed = end_time - start_time
        print(f"""Client {client_id} received result (Time taken: {time_elapsed:.2f}s).
Input tokens: {token_counter['input_tokens']}, speed {token_counter['input_tokens']/(time_elapsed):.2f} tokens/s,
Output tokens: {token_counter['output_tokens']}, speed {token_counter['output_tokens']/(time_elapsed):.2f} tokens/s
""")
        
        with open(f"outputs/tree/{client_id}.json", "w") as f:
            json.dump({
                "tree": root.to_dict(),
                "traj": chosen.trajectory
            }, f, indent=4, ensure_ascii=False)
    except TimeoutError as e:
        print(f"Client {client_id} timed out: {e}")
    except Exception as e:
        from traceback import format_exc
        print(format_exc())
        print(f"Client {client_id} encountered an error: {e}")
    finally:
        middleware.cancel_pending_messages()
    return token_counter

if __name__ == "__main__":
    import hydra
    from omegaconf import DictConfig, OmegaConf
    @hydra.main(config_path="conf", config_name="client", version_base=None)
    def main(cfg: DictConfig):
        global config
        OmegaConf.resolve(cfg)
        config = cfg
        print("Configuration:")
        print(OmegaConf.to_yaml(cfg))

        # 模拟多个客户端请求
        with open(cfg.dataset.path, "r") as f:
            data = [json.loads(line) for line in f.readlines()]
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(config.dataset.tokenizer)

        TEMPLATE = """Solve the following math problem step by step. Use double newlines as the end of each step. The last line of your response should be of the form Answer: $Answer (without quotes) where $Answer is the answer to the problem.

{Question}

Remember to put your answer on its own line after "Answer:". 

Here your step by step reponse:
"""
        initial_nodes = [tokenizer.apply_chat_template([
                            {"content":"You are a helpful assistant.","role":"system"},
                            {"content":TEMPLATE.format(Question=d['problem']),"role":"user"}
                        ], add_generation_prompt=True, tokenize=False) 
                        for d in data[:10]]
        root_nodes = [TreeNode(value) for value in initial_nodes]
        import os
        print(f"Output directory: {os.path.join(os.getcwd(), 'outputs')}")
        from tqdm.contrib.concurrent import process_map
        # 传递 config 到 client_request
        start_time = time.time()
        token_counters = process_map(client_request, enumerate(root_nodes), max_workers=cfg.client.max_workers, total=len(root_nodes))
        end_time = time.time()
        time_elapsed = end_time - start_time
        merged_counter = Counter()
        for counter in token_counters:
            merged_counter.update(counter)
        print(f"""Total time taken: {time_elapsed:.2f}s.
Total input tokens: {merged_counter['input_tokens']}, speed {merged_counter['input_tokens']/(time_elapsed):.2f} tokens/s, 
Total output tokens: {merged_counter['output_tokens']}, speed {merged_counter['output_tokens']/(time_elapsed):.2f} tokens/s""")
    main()