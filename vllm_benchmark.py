import ray
from omegaconf import DictConfig, OmegaConf

from redis_util.redis_worker import RedisServer

from model import VLLMModel
import json

def time_test(vllm, data):
    import time
    start = time.time()
    if len(data) < 100:
        times = 10
    else:
        times = 3
    refs = []
    for i in range(times):
        refs.append(vllm.process.remote(data))
    times_results = ray.get(refs)
    end = time.time()
    time_elapsed = end - start
    time_elapsed /= times
    counter = {
        "input_tokens": 0,
        "output_tokens": 0
    }
    for results in times_results:
        for r in results:
            r = json.loads(r)
            counter["input_tokens"] += r["input_tokens"]
            counter["output_tokens"] += r["output_tokens"]
    counter["input_tokens"] /= times
    counter["output_tokens"] /= times
    print(f"""Total time taken: {time_elapsed :.2f}s.
Total input tokens: {counter["input_tokens"]}, speed {counter['input_tokens'] / time_elapsed :.2f} tokens/s, 
Total output tokens: {counter["output_tokens"]}, speed {counter['output_tokens'] / time_elapsed :.2f} tokens/s""")

if __name__ == "__main__":
    import hydra
    from omegaconf import DictConfig, OmegaConf

    @hydra.main(config_path="conf", config_name="inference_server", version_base=None)
    def main(cfg: DictConfig):
        print("Configuration:")
        print(OmegaConf.to_yaml(cfg))
        import os
        print(os.getcwd())
        ray.init(address="auto", ignore_reinit_error=True,runtime_env={"working_dir": os.getcwd(),"excludes": ["outputs", 'dump.rdb']})
        with open(cfg.get("dataset"), "r") as f:
            data = [json.loads(line) for line in f.readlines()]
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(cfg.llm.model)

        TEMPLATE = """Solve the following math problem step by step. Use double newlines as the end of each step. The last line of your response should be of the form Answer: $Answer (without quotes) where $Answer is the answer to the problem.

{Question}

Remember to put your answer on its own line after "Answer:". 

Here your step by step reponse:
"""
        data = [tokenizer.apply_chat_template([
                            {"content":"You are a helpful assistant.","role":"system"},
                            {"content":TEMPLATE.format(Question=d['problem']),"role":"user"}
                        ], add_generation_prompt=True, tokenize=False) 
                        for d in data[:1000]]
        vllm = ray.remote(num_gpus=1)(VLLMModel).remote(
            config=cfg,
        )
        ret = ray.get(vllm.process.remote(["hello"]))
        print(f"ret: {ret}")
        for batch in [200,400,800]:
            time_test(vllm, data[:batch])

    main()