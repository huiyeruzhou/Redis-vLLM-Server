from interface import ModelInterface
from omegaconf import DictConfig
import timeit


# 实现一个简单的子类
class SimpleModel(ModelInterface):
    def __init__(self, config):
        super().__init__(config)

    def process(self, batch_tasks: list) -> list:
        # 模拟处理逻辑：将每个字符串转换为大写
        return [task.upper() for task in batch_tasks]


#  性能测试
def test_performance():
    # 创建 SimpleModel 实例
    model = SimpleModel(DictConfig({}))

    # 测试数据
    batch_tasks = ["hello", "world", "python", "performance", "test"] * 10

    # 测试 process 方法
    def test_process():
        model.process(batch_tasks)

    # 测试 process_bytes 方法
    def test_process_bytes():
        model.process_bytes([task.encode("utf-8") for task in batch_tasks])

    # 使用 timeit 测量性能
    process_time = timeit.timeit(test_process, number=1000000)
    process_bytes_time = timeit.timeit(test_process_bytes, number=1000000)

    # 打印结果
    print(f"process average time: {process_time :.6f} microseconds per call")
    print(f"process_bytes average time: {process_bytes_time :.6f} microseconds per call")


# 运行测试
if __name__ == "__main__":
    test_performance()
    # 每次请求有6微秒花费在字符串重复序列化/反序列化上，但是这个时间还包含无法避免的encode/decode操作（在redis中完成）
    # 整体代价完全可以接受，效果是ModelInterface通过适当重载可以承载任何数据类型（比如protobuf编码的int list for reward model），而不仅仅是字符串。
    # rocess average time: 1.488398 microseconds per call
    # process_bytes average time: 7.886504 microseconds per call
