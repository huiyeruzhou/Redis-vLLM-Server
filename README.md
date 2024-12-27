# Redis-vLLM-Server

## 项目简介

这个项目利用 redis 实现了客户端和服务端解耦的 llm 服务，并且在服务端进行了请求批量化以最大程度利用 vllm 的超线性加速。
对于需要少量多次调用本地 llm，但无法在客户端批量化请求的应用来说，这将提供近似完全并行且充分批量化的推理加速。
项目的经典用例是用于服务树搜索。

## 亮点

- **客户端-服务端解耦**：基于 Redis 实现的消息队列，支持多对多的高性能并发服务。
- **超线性加速**：基于 vLLM 部署的推理引擎从任务队列中批量获取任务，通过聚合大量碎片化请求，实现超线性加速。
- **分布式调度**：基于 Ray 实现的分布式调度器，支持多机多卡环境下灵活配置并行策略，拉起常驻 Worker 快速响应请求。
- **优雅退出**: Redis Stream提供了删除消息的功能，客户端退出时可以主动删除消息，避免无效请求堆积。

## 性能试验

在32卡节点上试验，使用async_client在5000条样本上运行宽度为5深度为3的波束搜索算法，估值函数的消耗可忽略不计，设置目标batchsize=200，吞吐量能达到**32倍单卡极限吞吐量的55%**：78501/32/4473 = 0.548，这里使用的极限吞吐量对应于**batchsize=1000**时的单卡vllm推理速度。

考虑到搜索是高度碎片化的，并且redis中间件和具体的搜索算法或模型服务完全解耦，（修改redis_utils中的内容不会影响clien和server的代码），这还是相当了不起速度。

可以想象，若不能有效地将请求批量化并使用合适的编程模型完成响应分发，即使使用更大规模的集群，树搜索的效率也将会大大下降。在那种情况下，请求必须手动分配给某个worker，无法进行负载均衡，且每个worker总是在处理少量的碎片请求。


## 整体架构

以下是项目的时序图：

![工作流程](http://plantuml.com/plantuml/svg/TLDTQzDG6BxFhtWTmjom1J_qeZ0PUEF5l76tWt7MlBIXQHBFEPdJCR1Zgr5hMvaA6qNSwA1OI17KThJ5FzDpabxv5pnTegcrzgRd9E_pyJmlMMMSKEuMJUqApsCH8OlKDP5OciODRY8yGjrWgUsrjOUfTQJRI45qpqV3XlnM2bglDMtBte45uPz9hnkqEmiQg9-ZA8siuH2BmttJGFL7M7pIqD91WMBa9Vq_g4XrdLCaHGMTliuVVK1ONWqcbnaPsNWZNftEDHYd8Ym-9SZOUkhUNtOoS2CDelPs_BmKThhooxwe78fwMpHFHhtFu52xw708JWwYbnlMjHsYwx2uomahR1hwodMAZrra_FAi4qvMzXQNO1aiRLf5YSR6Cd0p3H7x4viMa0glBBosV7anVb4BgrrH-o2_UxBKcKWfh9wZjkj0rdQLzwkxMuHdyvlCU8PxIrfCEKr-AmsZuO_WNGicEmPPRGNriXYybqVL3IiI4Sq3zMpKxwiA4ugPKcwd8PeGVaLwzP-3Id3gXtvJUGk_NYGEIRO0HPPJwQVj94lCm1qLjtmi3i-3_f7ys52WF4CElsihu5E8krIjKcIkoISLpguhPhkRXd3eXvspX2qtdorQml-MlWnYi2zwlQWC5KzudyR7pIIOZYPo_9Dj5IrTVLQ_0000)

<!-- ```plantuml
@startuml
!theme cerulean

title Redis-vLLM-Server 工作流程

box "客户端"
    participant Client as Client
end box

box "Redis"
    participant RedisStream as RedisStream
    participant RedisHash as RedisHash
end box

box "服务端"
    participant Server as Server
end box

Client -> RedisStream : 1. 发送请求包含多个 prompt 的请求\n(xadd '{task_id=,request_id=,data=}') * n
RedisStream -> Server : 2. 多个worker分别批量获取消息\n(xreadgroup count ${batch_size})
Server -> Server : 3. 使用 vLLM 推理引擎\n批量处理任务
Server -> RedisStream : 4. 任务完成，确认消费消息\n(xack msg_id)
Server -> RedisHash : 5. 写入结果\n(hset request_key task_id '{result=}')
Client -> RedisHash : 6. 轮询结果\n(hget request_key task_id) * n
Client -> RedisStream : * 异常处理，删除消息\n(xdelete msg_id)
@enduml
``` -->

## 快速开始

### Ray 集群部署

1. **单机环境**：

   ```bash
   ray start --head --port=6379
   ```

2. **多机环境**：
   - 在主机上启动 Ray 头节点：

        ```bash
        ray start --head --port=6379
        ```

   - 在其他机器上启动 Ray 工作节点，连接到头节点：

        > 在 Kubernetes 环境下，确保所用的端口已经暴露给外部机器。

        ```bash
        ray start --address=<head-node-ip>:6379
        ```

### 启动 Redis Server

1. **安装 Redis**：

   ```bash
   sudo apt-get install redis-server
   ```

2. **启动 Redis**：
   这里由于 Ray 已经占用了 6379 端口，因此需要使用一个新的端口启动 redis
   - 单机环境：

        ```bash
        redis-server --port 6666
        ```

   - 多机环境（下列代码将开放任意 ip 对 redis 的访问，请**确保访问受控**）：

        ```bash
        redis-server --bind 0.0.0.0 :: --protected-mode no --port 6666
        ```

        > 在 Kubernetes 环境下，确保所用的端口已经暴露给外部机器。

### 填写 `conf.yaml`

配置文件 `conf/redis.yaml` 包含以下关键配置项：

```yaml
redis:
  host: ${oc.env:MY_REDIS_HOST}  # Redis 主机, 从环境变量中获取，也可固定，注意多机环境下不要写localhost
  port: ${oc.env:MY_REDIS_PORT}  # Redis 端口，同上，注意多机环境下确保端口外部可见
  expire_seconds: 300            # 消息过期时间（秒）
  timeout_seconds: 1800          # 客户端超时时间（秒）
```

配置文件 `conf/inference_server.yaml`：

```yaml
defaults:
  - redis.yaml

worker:
  num_gpus: 32
  batch_size: 200
  poll_interval_milis: 1000         # worker从队列中尝试获取消息的超时事件
  stream_name: infer_task_stream    # 消息队列名称
  group_name: infer_group           # 消费者组名称
  results_prefix: "infer_result:"   # 结果Hash的key的前缀

llm:
  model: "/path/to/model"           # 替换为实际的模型名称
  tokenizer: ~                  
  tensor_parallel_size: 1 
  gpu_memory_utilization: 0.5 
  n: 5
  max_tokens: 512
  temperature: 1
  top_k: -1
  top_p: 1.0
  stop: ["\n\n", "<|EOS_TOKEN|>"]   # 这里的EOS_TOKEN可以自动替换为实际的tokenizer中标记的eos_token_id对应的所有token
```

配置文件 `conf/client.yaml`：

```yaml
defaults:
  - redis.yaml
  - ".@inference_server": inference_server.yaml # inference_server.yaml中的配置被导入到当前配置的“inference_server”这个key下

client:
  max_workers: 1000 # 并发请求的最大数量

dataset:
  tokenizer: ${inference_server.llm.model}
  path: /path/to/dataset.jsonl
```

### 启动 Server

1. 启动 Ray 集群（如未启动）。
2. 运行 `redis_server.py`：

   ```bash
   python redis_server.py
   ```

### 启动 Client

1. 运行 `redis_client.py`：

   ```bash
   python redis_client.py
   ```

2. 客户端将自动发送请求并等待结果。

## PERF

### 普通ray worker

这组基准测试展示了vllm自带的超线性加速效果，对于小规模的请求，vllm的速度会随着batch size的增加而增加。

```bash
Processed prompts: 100%|██████████| 1/1 [00:01<00:00,  1.05s/it, est. speed input: 110.51 toks/s, output: 334.38 toks/s]
Total time taken: 0.87s.
Total input tokens: 116.0, speed 133.01 tokens/s, 
Total output tokens: 321.4, speed 368.54 tokens/s

Processed prompts:  90%|█████████ | 9/10 [00:04<00:00,  1.30it/s, est. speed input: 255.44 toks/s, output: 542.68 toks/s]
Total time taken: 3.29s.
Total input tokens: 1400.0, speed 425.88 tokens/s, 
Total output tokens: 2495.6, speed 759.15 tokens/s

Processed prompts:  98%|█████████▊| 98/100 [00:09<00:00,  3.18it/s, est. speed input: 1392.61 toks/s, output: 2737.44 toks/s]
Total time taken: 10.70s.
Total input tokens: 13876.0, speed 1296.81 tokens/s, 
Total output tokens: 30312.0, speed 2832.87 tokens/s

Processed prompts: 100%|██████████| 200/200 [00:16<00:00, 12.42it/s, est. speed input: 1783.76 toks/s, output: 3620.01 toks/s]
Total time taken: 15.54s.
Total input tokens: 28715.0, speed 1848.30 tokens/s, 
Total output tokens: 56679.0, speed 3648.27 tokens/s

Processed prompts: 100%|██████████| 400/400 [00:28<00:00, 14.22it/s, est. speed input: 2061.88 toks/s, output: 4130.95 toks/s]
Total time taken: 28.80s.
Total input tokens: 57980.0, speed 2012.88 tokens/s, 
Total output tokens: 118769.0, speed 4123.27 tokens/s

Processed prompts: 100%|██████████| 800/800 [00:50<00:00, 15.98it/s, est. speed input: 2290.86 toks/s, output: 4466.77 toks/s]
Total time taken: 50.93s.
Total input tokens: 114700.0, speed 2252.02 tokens/s, 
Total output tokens: 225741.33333333334, speed 4432.20 tokens/s

Processed prompts: 100%|█████████▉| 999/1000 [01:02<00:00,  4.33it/s, est. speed input: 2298.45 toks/s, output: 4518.68 toks/s]
Total time taken: 63.72s.
Total input tokens: 143163.0, speed 2246.91 tokens/s, 
Total output tokens: 285030.3333333333, speed 4473.48 tokens/s
```

### 1000条数据，async

- stream
Total time taken: 69.85s.
Total input tokens: 3693311, speed 52877.42 tokens/s, 
Total output tokens: 3065440, speed 43888.14 tokens/s

- hash
Total time taken: 66.19s.
Total input tokens: 3715998, speed 56139.88 tokens/s, 
Total output tokens: 3111921, speed 47013.71 tokens/s

- list
Total time taken: 69.91s.
Total input tokens: 3725910, speed 53298.25 tokens/s, 
Total output tokens: 3078242, speed 44033.52 tokens/s

### 5000条数据，async
- list
Total time taken: 196.71s.
Total input tokens: 18581520, speed 94459.87 tokens/s, 
Total output tokens: 15332219, speed 77941.92 tokens/s

- hash
Total time taken: 209.66s.
Total input tokens: 18545008, speed 88454.05 tokens/s, 
Total output tokens: 15419857, speed 73548.03 tokens/s

- stream
Total time taken: 196.00s.
Total input tokens: 18527902, speed 94532.40 tokens/s, 
Total output tokens: 15385884, speed 78501.31 tokens/s

### 其他配置  

- 100条数据/100并发请求 ｜ 32gpus * batch=100，hash

```bash
100%|███████████████████████████████████████████████████████████████████████████████████████████████████████████████| 100/100 [00:32<00:00,  3.04it/s]
Total time taken: 33.57s.
Total input tokens: 372614, speed 11100.78 tokens/s, 
Total output tokens: 331751, speed 9883.41 tokens/s
```

- 100条数据/100并发请求｜ 32gpus * batch=200，hash
```
Total time taken: 45.21s.
Total input tokens: 368698, speed 8155.53 tokens/s, 
Total output tokens: 303107, speed 6704.67 tokens/s
```

- 1000条数据/100并发请求｜ 32gpus * batch=100，hash

```bash
100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████| 1000/1000 [02:55<00:00,  5.69it/s]
Total time taken: 176.48s.
Total input tokens: 3702776, speed 20981.08 tokens/s, 
Total output tokens: 3084851, speed 17479.73 tokens/s
```

- 1000条数据/400并发请求 ｜ 32gpus * batch=100，hash

```bash
100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████| 1000/1000 [01:31<00:00, 10.92it/s]
Total time taken: 95.76s.
Total input tokens: 3734685, speed 38999.43 tokens/s, 
Total output tokens: 3068975, speed 32047.76 tokens/s
```

- 1000条数据/async vs 32gpus * batch=200，hash

```bash
Total time taken: 66.45s.
Total input tokens: 3716021, speed 55924.87 tokens/s, 
Total output tokens: 3153149, speed 47453.83 tokens/s
```

- 5000条数据/400并发请求 vs 32gpus * batch=100

```bash
100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████| 5000/5000 [06:23<00:00, 13.05it/s]
Total time taken: 388.06s.
Total input tokens: 18561332, speed 47830.99 tokens/s, 
Total output tokens: 15459100, speed 39836.80 tokens/s

100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████| 5000/5000 [06:21<00:00, 13.10it/s]
Total time taken: 385.81s.
Total input tokens: 18524192, speed 48014.20 tokens/s, 
Total output tokens: 15257180, speed 39546.20 tokens/s
```

- 5000条数据/1000并发请求 vs 32gpus * batch=200

```bash
100%|█████████████████████████████████████████████████████████████████████████████████████████████████████████████| 5000/5000 [04:08<00:00, 20.14it/s]
Total time taken: 276.34s.
Total input tokens: 18618908, speed 67377.65 tokens/s, ->  2105 tokens/gpu/s
Total output tokens: 15541055, speed 56239.59 tokens/s ->  1757 tokens/gpu/s
```
