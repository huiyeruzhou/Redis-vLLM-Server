import asyncio
import uuid
import logging
from typing import List
from omegaconf import DictConfig
import redis.asyncio as redis
import time
import uuid
import logging
import json

class AsyncRedisMiddleWare:
    """
    RedisMiddleWare 类用于将请求发送到 Redis 流中，并根据请求的 ID 从 Redis 中获取结果。
    使用 Redis 连接池优化连接管理。
    """
    def __init__(self, config: DictConfig, worker: DictConfig, name: str = None):
        self.redis = None
        self.client_stream = config.redis.client_stream
        self.config = config
        self.timeout = config.redis.timeout_seconds
        self.stream_name = worker.stream_name
        self.results_prefix = worker.results_prefix
        self.pending_messages = {}
        self.name = name or f"RedisMiddleware_{int(time.time())}"
        self.logger = logging.getLogger(self.name)
    
    async def initialize(self):
        # 创建 Redis 连接池
        self.redis = await redis.Redis(host=self.config.redis.host, port=self.config.redis.port, db=0, decode_responses=True)
        # self.redis = await redis.create_redis_pool(
        #     (self.config.redis.host, self.config.redis.port),
        #     db=0,
        #     encoding='utf-8',  # 自动解码返回的数据
        #     maxsize=10         # 设置连接池的最大连接数
        # )

    async def exit_handler(self, signum, frame):
        self.logger.info(f"Received signal: {signum}")
        await self.cancel_pending_messages()
        self.redis.close()
        await self.redis.wait_closed()
        exit(0)

    async def process_requests(self, trajectories: List[str]) -> List[str]:
        """
        处理请求，将任务发送到 Redis Stream 并等待结果。
        :param trajectories: 任务数据列表
        :return: 任务结果列表
        """
        request_id = str(uuid.uuid4())
        task_ids = [str(uuid.uuid4()) for _ in trajectories]
        tasks = []

        # 构建任务数据
        for task_id, trajectory in zip(task_ids, trajectories):
            task = {
                'data': trajectory,
                'request_id': request_id, 
                'task_id': task_id
            }
            tasks.append(task)

        # 使用 pipeline 批量发送任务到 Redis Stream
        for task in tasks:
            await self.redis.xadd(self.stream_name, task)

        # 记录已发送的消息 ID
        for task in tasks:
            self.pending_messages[task['task_id']] = task['task_id']
            # self.logger.info(f"Sent task {task['task_id']} to Redis Stream")

        # 等待并获取结果
        result_key = self.results_prefix + request_id
        self.logger.debug(f"Sent {len(tasks)} tasks to Redis Stream with result_key '{result_key}'")
        results = await self._wait_for_results(task_ids, result_key)
        return results

    async def _wait_for_results(self, task_ids: List[str], result_key: str) -> List[str]:
        """
        等待任务结果，直到所有任务完成或超时。
        :param task_ids: 任务 ID 列表
        :param result_key: 结果键
        :return: 任务结果列表
        """
        start_time = asyncio.get_event_loop().time()
        results = {}

        while len(results) < len(task_ids):
            blocktime = start_time + self.timeout - asyncio.get_event_loop().time()
            if blocktime <= 0:
                raise TimeoutError("Timed out waiting for task results")
            if self.client_stream == "list":
                result = await self.redis.blpop(result_key, timeout=int(blocktime))  # 阻塞式获取结果
                if result:
                    data = json.loads(result[1])
                    results[data['task_id']] = data['result']
            elif self.client_stream == "stream":
                stream_messages = await self.redis.xread({result_key: '0-0'}, count=len(task_ids), block=int(blocktime * 1000))
                if stream_messages:
                    for stream_name, messages in stream_messages:
                        for message in messages:
                            data = message[1]
                            results[data['task_id']] = data['result']
            elif self.client_stream == "hash":
                fetched = await self.redis.hgetall(result_key)
                for task_id, result in fetched.items():
                    if task_id not in results:
                        results[task_id] = result
                        self.pending_messages.pop(task_id)
                # 异步等待
                if len(results) < len(task_ids):
                    await asyncio.sleep(0.1)

        # 删除结果键以释放内存
        await self.redis.delete(result_key)
        return [results[task_id] for task_id in task_ids]

    async def cancel_pending_messages(self):
        """
        取消所有未处理的消息。
        """
        if self.pending_messages:
            for message_id in self.pending_messages.values():
                await self.redis.xdel(self.stream_name, message_id)
            self.pending_messages.clear()
            self.logger.info(f"All pending messages canceled.")
        else:
            self.logger.info(f"No pending messages to cancel.")

    async def __aexit__(self, exc_type, exc, tb):
        """
        异步上下文管理器退出时关闭 Redis 连接池。
        """
        self.redis.close()
        await self.cancel_pending_messages()
        await self.redis.wait_closed()
        self.logger.debug(f"Redis connection pool closed.")