import redis
import time
import uuid
import logging
from typing import List, Type, Generic, TypeVar
from omegaconf import DictConfig

from .codec import response_from_byte
from .interface import ModelInterface

I = TypeVar("I")
O = TypeVar("O")
class RedisMiddleWare(Generic[I, O]):
    """
    RedisMiddleWare 类用于将请求发送到 Redis 流中，并根据请求的 ID 从 Redis 中获取结果。
    使用 Redis 连接池优化连接管理。
    """

    def __init__(self, config: DictConfig, worker: DictConfig, model_cls: Type[ModelInterface[I, O]],name: str = ""):
        # 创建 Redis 连接池
        self.client_stream = config.redis.client_stream
        self.redis_pool = redis.ConnectionPool(
            host=config.redis.host, port=config.redis.port, db=0, decode_responses=False, max_connections=config.redis.max_connections  # 设置连接池的最大连接数
        )
        self.redis = redis.Redis(connection_pool=self.redis_pool)
        self.timeout = config.redis.timeout_seconds
        self.stream_name = worker.stream_name
        self.results_prefix = worker.results_prefix
        self.pending_messages: dict[str, str] = {}  # mapping task_id to message_id, used to cancel pending messages in graceful exit
        self.name = name or f"RedisMiddleware_{int(time.time())}"
        self.logger = logging.getLogger(self.name)
        self.model_cls = model_cls

    def exit_handler(self, signum, frame):
        self.logger.info(f"Received signal: {signum}")
        self.cancel_pending_messages()
        self.redis_pool.disconnect()
        exit(0)

    def process_requests(self, inputs: List[I]) -> List[O]:
        """
        处理请求，将任务发送到 Redis Stream 并等待结果。
        :param inputs: 任务数据列表
        :return: 任务结果列表
        """
        request_id = str(uuid.uuid4())
        task_ids = [str(uuid.uuid4()) for _ in inputs]
        encoded_inputs = self.model_cls.encode_inputs(inputs)

        # 构建任务数据
        tasks = []
        for task_id, encoded_input in zip(task_ids, encoded_inputs):
            task = {"data": encoded_input, "request_id": request_id, "task_id": task_id}
            tasks.append(task)

        # 使用 pipeline 批量发送任务到 Redis Stream
        with self.redis.pipeline() as pipe:
            for task in tasks:
                pipe.xadd(self.stream_name, task)
            messages = pipe.execute()

        # 记录已发送的消息 ID
        for message_id, task_id in zip(messages, task_ids):
            self.pending_messages[task_id] = message_id
            # self.logger.info(f"Sent task {task_id} to Redis Stream with message ID {message_id}")

        # 等待并获取结果
        result_key = self.results_prefix + request_id
        self.logger.debug(f"Sent {len(tasks)} tasks to Redis Stream with result_key '{result_key}'")
        results = self._wait_for_results(task_ids, result_key)
        return results

    def _wait_for_results(self, task_ids: List[str], result_key: str) -> List[O]:
        """
        从 Redis List 或 Stream 或 Hash 中获取结果，直到所有任务完成或超时。
        """
        start_time = time.time()
        results = {}

        while len(results) < len(task_ids):
            blocktime = start_time + self.timeout - time.time()
            if blocktime <= 0:
                raise TimeoutError("Timed out waiting for task results")

            if self.client_stream == "list":
                result = self.redis.blpop(result_key, timeout=int(blocktime))  # 阻塞式获取结果
                if result:
                    data = response_from_byte(result[1])
                    results[data.task_id] = data.result
            elif self.client_stream == "stream":
                stream_messages = self.redis.xread({result_key: "0-0"}, count=len(task_ids), block=int(blocktime * 1000))
                if stream_messages:
                    for stream_name, messages in stream_messages:
                        for message in messages:
                            data = message[1]
                            results[data[b"task_id"].decode()] = data[b"result"]
            elif self.client_stream == "hash":
                # 使用 pipeline 批量获取任务结果
                fetched = self.redis.hgetall(result_key)
                for task_id, result in fetched.items():
                    task_id = task_id.decode()
                    if task_id not in results:
                        results[task_id] = result
                        self.pending_messages.pop(task_id)
                # 轮询等待
                if len(results) < len(task_ids):
                    time.sleep(0.1)
            else:
                raise ValueError(f"Invalid client_stream value: {self.client_stream}")

        # 删除结果键以释放内存
        self.redis.delete(result_key)
        return self.model_cls.decode_outputs([results[task_id] for task_id in task_ids])

    def cancel_pending_messages(self):
        """
        取消所有未处理的消息。
        """
        if self.pending_messages:
            with self.redis.pipeline() as pipe:
                for message_id in self.pending_messages.values():
                    pipe.xdel(self.stream_name, message_id)
                pipe.execute()
            self.pending_messages.clear()
            self.logger.info(f"All pending messages canceled.")
        else:
            self.logger.info(f"No pending messages to cancel.")

    def __del__(self):
        """
        析构函数，关闭 Redis 连接池。
        """
        self.redis_pool.disconnect()
        self.logger.debug(f"Redis connection pool closed.")
