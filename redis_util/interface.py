from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from omegaconf import DictConfig
import pickle

# 定义 ModelInterface 抽象基类
I = TypeVar('I')
O = TypeVar('O')
class ModelInterface(ABC, Generic[I, O]):
    """
    ModelInterface 类是一个抽象基类，用于定义模型接口。
    它定义了一个 process 方法，用于处理输入数据并返回输出结果。
    它还定义了适用于 input和output 的 encode/decode方法。
    默认情况下encode/decode适用于str并采用utf-8编码，更改数据时需要重写这两个方法。
    """

    def __init__(self, config: DictConfig):
        pass

    @abstractmethod
    def process(self, batch_tasks: list[I]) -> list[O]:
        pass

    def process_bytes(self, batch_tasks: list[bytes]) -> list[bytes]:
        batch_tasks = self.decode_inputs(batch_tasks)
        results = self.process(batch_tasks)
        return self.encode_outputs(results)

    @staticmethod
    def encode_outputs(value: list[O]) -> list[bytes]:
        return [pickle.dumps(item) for item in value]

    @staticmethod
    def decode_outputs(value: list[bytes]) -> list[O]:
        return [pickle.loads(item) for item in value]

    @staticmethod
    def encode_inputs(value: list[I]) -> list[bytes]:
        return [pickle.dumps(item) for item in value]
    
    @staticmethod
    def decode_inputs(value: list[bytes]) -> list[I]:
        return [pickle.loads(item) for item in value]
