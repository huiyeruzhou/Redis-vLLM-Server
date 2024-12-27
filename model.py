import time
from typing import List
from omegaconf import DictConfig
from vllm import LLM, SamplingParams  # 确保 vllm 库已正确安装
from typing_extensions import override
from redis_util.redis_worker import ModelInterface
from transformers import AutoTokenizer
import json
from pydantic import BaseModel


class VLLMProtocol(BaseModel):
    texts: List[str]
    output_tokens: int
    input_tokens: int

class VLLMModel(ModelInterface):
    """
    VLLMModel 类根据给定的配置初始化一个 vllm.LLM 模型，并根据配置中的参数接受 prompt 和推理
    """
    def __init__(self, config: DictConfig):
        """
        初始化 VLLMModel 类的实例。
        :param config: 包含模型配置的 OmegaConf 配置对象
        """
        self.tokenizer = AutoTokenizer.from_pretrained(
            config.llm.model,
            trust_remote_code=True,
            use_fast=False,
        )
        self.llm = LLM(model=config.llm.model,
                     tensor_parallel_size=config.llm.tensor_parallel_size,
                     gpu_memory_utilization=config.llm.gpu_memory_utilization)  
        # 处理 stop 参数, 将其中的 <|EOS_TOKEN|> 替换为实际的 EOS 标记（根据eos_token_id来确定）
        config.llm.stop = self.parse_stop(config.llm.stop)
        self.sampling_params = SamplingParams(
            n=config.llm.n,
            max_tokens=config.llm.max_tokens,
            temperature=config.llm.temperature,
            top_k=config.llm.top_k,
            top_p=config.llm.top_p,
            stop=config.llm.stop,
        )
        print(f"Initialized VLLMModel with model {config.llm.model}")
        print(f"Initialized VLLMModel with sampling_params {self.sampling_params}")
    
    @override
    def process(self, batch_tasks: List[str]) -> List[str]:
        responses = self.llm.generate(batch_tasks, sampling_params=self.sampling_params)
        protocols = [VLLMProtocol(texts=[o.text for o in rsp.outputs], 
                                output_tokens=sum(len(o.token_ids) for o in rsp.outputs), 
                                input_tokens=len(rsp.prompt_token_ids)) for rsp in responses]
        return [protocol.model_dump_json() for protocol in protocols]
    
    def parse_stop(self, stop):
        if isinstance(stop, str):
            stop = [stop]
        if '<|EOS_TOKEN|>' in stop:
            eos_id = self.tokenizer.eos_token_id
            stop.remove('<|EOS_TOKEN|>')
            if isinstance(eos_id, int):
                stop.append(self.tokenizer.decode(eos_id))
            elif isinstance(eos_id, list):
                stop.extend([self.tokenizer.decode(e) for e in eos_id])
            else:
                raise ValueError(f"Invalid eos_id type: {type(eos_id)}")
        return stop
                

class DummyModel:
    """
    DummyModel 类用于模拟模型的处理过程。它接受一个字符串列表作为输入，并返回一个字符串列表作为输出。
    """
    def __init__(self, config: DictConfig):
        self.n = config.llm.n

    @override
    def process(self, batch_tasks: List[str]) -> List[List[str]]:
        # 模拟超线性的处理速度
        time.sleep(1 * len(batch_tasks) ** 0.8)
        return [["response" + str(i) for i in range(self.n)]] * len(batch_tasks)


