import ray
import time
import logging
from typing import List, Dict, Type
from omegaconf import DictConfig
import redis

from .codec import response_to_byte
from .interface import ModelInterface


# 定义 RedisWorker Actor
@ray.remote(num_cpus=1, runtime_env={"pip": ["redis"]})
class RedisWorker:
    """
    RedisWorker 类用于处理 Redis 队列中的任务。它使用 group 以实现多个消费者并发执行。
    每个消费者从 Redis 队列中获取任务，并使用 ModelInterface 实现类进行处理。
    处理完成后，发送 ack 并将结果写入 Redis Hash。
    """

    def __init__(self, config: DictConfig, consumer_name: str, ModelImpl: Type[ModelInterface]):
        # 创建 Redis 连接池
        self.redis_pool = redis.ConnectionPool(
            host=config.redis.host, port=config.redis.port, db=0, decode_responses=False, max_connections=config.redis.max_connections  # 设置连接池的最大连接数
        )
        self.redis = redis.Redis(connection_pool=self.redis_pool)
        self.client_stream = config.redis.client_stream
        self.stream_name = config.worker.stream_name
        self.results_prefix = config.worker.results_prefix
        self.group_name = config.worker.group_name
        self.consumer_name = consumer_name or f"consumer_{int(time.time())}"
        self.model = ModelImpl(config)
        self.batch_size = config.worker.batch_size
        self.poll_interval_milis = config.worker.poll_interval_milis
        self.expire_seconds = config.redis.expire_seconds
        self.logger = logging.getLogger(self.consumer_name)

    def create_consumer_group(self):
        """
        创建消费者组。如果消费者组已经存在，则忽略错误。
        """
        try:
            self.redis.xgroup_create(name=self.stream_name, groupname=self.group_name, id="0", mkstream=True)
            self.logger.info(f"Created consumer group '{self.group_name}'")
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" in str(e):
                self.logger.info(f"Consumer group '{self.group_name}' already exists")
            else:
                self.logger.error(f"Failed to create consumer group - {e}")
                raise e

    def process_batch(self, tasks: List[Dict]):
        """
        处理一批消息。从消息中提取数据、结果键和任务 ID，并使用模型处理数据。
        处理完成后，将结果存储到 Redis Hash 中。返回消息 ID 列表。
        """
        data, request_ids, task_ids = [], [], []
        for task in tasks:
            task_ids.append(task[b"task_id"].decode())
            data.append(task[b"data"])
            request_ids.append(task[b"request_id"].decode())

        self.logger.info(f"Processing batch of {len(data)} prompts")

        # 处理任务
        results = self.model.process_bytes(data)
        assert isinstance(results, list), f"Model.process should return a list, but got {type(results)}"
        # assert isinstance(results[0], str), (
        #     f"Model.process should return a list of strings, but got {type(results[0])}")

        # 将结果推送到 Redis List 或 Stream 或 Hash 中
        with self.redis.pipeline() as pipe:
            for result, task_id, request_id in zip(results, task_ids, request_ids):
                result_key = self.results_prefix + request_id
                # 使用 Redis List
                if self.client_stream == "list":
                    pipe.rpush(result_key, response_to_byte(task_id=task_id, result=result))
                # 或者使用 Redis Stream
                elif self.client_stream == "stream":
                    pipe.xadd(result_key, {"result": result, "task_id": task_id})
                elif self.client_stream == "hash":
                    pipe.hset(result_key, task_id, result)
                else:
                    raise ValueError(f"Invalid client_stream type: {self.client_stream}")
                pipe.expire(result_key, self.expire_seconds)
            pipe.execute()

        self.logger.info(f"Completed batch")

    def run(self):
        """
        运行 Worker，持续从 Redis Stream 中读取任务并处理。
        """
        self.create_consumer_group()
        while True:
            try:
                # 从消费者组读取消息，阻塞等待新消息
                messages = self.redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_name: ">"},
                    count=self.batch_size,
                    block=self.poll_interval_milis,  # 毫秒
                )

                if messages:
                    for stream, msgs in messages:
                        if msgs:
                            msg_ids, tasks = zip(*msgs)
                            self.logger.info(f"Received {len(msgs)} messages")
                            self.process_batch(tasks)
                            # 处理完后，确认消息
                            self.redis.xack(self.stream_name, self.group_name, *msg_ids)
                            self.logger.info(f"Processed {len(msgs)} messages")
                else:
                    self.logger.debug(f"No messages received")
                    time.sleep(1)  # 等待一段时间再重试
            except Exception as e:
                self.logger.error(f"Encountered error - {e}")
                import traceback

                self.logger.error(f"{traceback.format_exc()}")
                if "NOGROUP" in str(e):
                    self.logger.warning(f"Consumer group '{self.group_name}' does not exist")
                    self.create_consumer_group()
                time.sleep(1)  # 等待一段时间再重试

    def __del__(self):
        """
        析构函数，关闭 Redis 连接池。
        """
        self.redis_pool.disconnect()
        self.logger.info(f"Redis connection pool closed.")


class RedisServer:
    def __init__(self, cfg: DictConfig) -> None:
        self.config = cfg
        self.logger = logging.getLogger("RedisServer")
        self.stream_name = cfg.worker.stream_name
        self.redis_client = redis.Redis(host=cfg.redis.host, port=cfg.redis.port, db=0)
        print(f"Redis server is alive: {self.redis_client.ping()}")
        # 删除这个worker此前产出的的结果
        for key in self.redis_client.scan_iter(f"{cfg.worker.results_prefix}*"):
            self.redis_client.delete(key)

    def exit_handler(self, signum, frame):
        try:
            self.logger.info(f"Received signal: {signum}")
            self.redis_client.close()
        except Exception as e:
            self.logger.error(f"Failed: {e}")
        exit(0)

    def delete_consumer_groups(self, stream_name, group_pattern=None):
        """
        删除指定 Redis 流的所有消费者组。如果提供了 group_pattern，则仅删除与模式匹配的组。
        :param stream_name: Redis 流的名称
        :param group_pattern: 可选的消费者组模，用于过滤要删除的组，通配符匹配
        """
        import fnmatch

        try:
            groups = self.redis_client.xinfo_groups(stream_name)
        except redis.exceptions.ResponseError as e:
            logging.error(f"Failed to list consumer groups: {e}")
            return
        if not groups:
            logging.info(f"No consumer groups found for stream '{stream_name}'.")
            return

        for group in groups:
            group_name = group["name"].decode("utf-8")
            if group_pattern and fnmatch.fnmatch(group_name, group_pattern):
                try:
                    self.redis_client.xgroup_destroy(stream_name, group_name)
                    logging.info(f"Consumer group '{group_name}' deleted successfully.")
                except redis.exceptions.ResponseError as e:
                    logging.error(f"Failed to delete consumer group '{group_name}': {e}")

    def run_workers(self, num_workers, ModelImpl: Type[ModelInterface]):
        """
        启动多个 Worker 来处理 Redis 队列中的任务。这个方法不会返回。
        :param num_workers: 要启动的 Worker 数量
        :param ModelImpl: 实现了模型接口的类
        """
        workers = []
        consumer_uid = f"consumer_{int(time.time())}"
        for i in range(num_workers):
            consumer_name = f"{consumer_uid}_{i}"
            worker = RedisWorker.options(
                num_gpus=self.config.llm.tensor_parallel_size,
            ).remote(config=self.config, consumer_name=consumer_name, ModelImpl=ModelImpl)
            workers.append(worker)
        # 启动 Worker 的 run 方法作为后台任务
        refs = [worker.run.remote() for worker in workers]
        print(f"Started {num_workers} worker.")
        ray.get(refs)
