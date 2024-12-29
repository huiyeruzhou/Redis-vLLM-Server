# Redis-vLLM-Server

## 项目简介

这个项目利用 redis 实现了客户端和服务端解耦的 llm 服务，并且在服务端进行了请求批量化以最大程度利用 vllm 的超线性加速。
对于需要少量多次调用本地 llm，但无法在客户端批量化请求的应用来说，这将提供近似完全并行且充分批量化的推理加速。

## 亮点

- **客户端-服务端解耦**：基于 Redis 实现的消息队列，支持多对多的高性能并发服务。
- **超线性加速**：基于 vLLM 部署的推理引擎从任务队列中批量获取任务，通过聚合大量碎片化请求，实现超线性加速。
- **分布式调度**：基于 Ray 实现的分布式调度器，支持多机多卡环境下灵活配置并行策略，拉起常驻 Worker 快速响应请求。
- **优雅退出**: Redis Stream提供了删除消息的功能，客户端退出时可以主动删除消息，避免无效请求堆积。

不过，由于项目使用redis流存储，所有的**请求信息**都将会一直保留在redis server机器的cpu内存中，可以使用redis-cli连接并用flushall命令在闲时删库清理（已发送的请求和未处理的返回值将全部丢失，拉起的llm不受到影响，流将会在llm下次轮询时自动重建，但您可能会看到报警日志）

## 性能试验

在32卡节点上试验，使用async_client在5000条样本上运行宽度为5深度为3的波束搜索算法，估值函数的消耗可忽略不计，设置目标batchsize=200，吞吐量能达到**32倍单卡极限吞吐量的55%**：78501/32/4473 = 0.548，这里使用的极限吞吐量对应于**batchsize=1000**时的单卡vllm推理速度。

考虑到搜索是高度碎片化的，并且redis中间件和具体的搜索算法或模型服务完全解耦，（修改redis_utils中的内容不会影响clien和server的代码），这还是相当了不起速度。

可以想象，若不能有效地将请求批量化并使用合适的编程模型完成响应分发，即使使用更大规模的集群，树搜索的效率也将会大大下降。在那种情况下，请求必须手动分配给某个worker，无法进行负载均衡，且每个worker总是在处理少量的碎片请求。

## 适用场景与设计理念

项目尤其适用于以下场景：

- **多对多生产/消费**：您正在部署多种llm，或者希望以多个客户端请求llm服务或者兼而有之。例如同时部署InferenceModel和RewardModel，并进行并发地Reward指导下的采样。
- **碎片化请求**：您正在进行无法轻易离线批量化的请求，例如会动态产生新请求的Tree of thought推理或其他推理
- **分布式环境**：您正在使用一个多机多卡的环境，需要创建多个llm的实例并将请求路由到这些llm上

在这些场景中，以下问题将会变得十分棘手：

1. 如何方便地创建并管理多个vllm model实例
2. 如何将请求路由到这些vllm上
3. 如何聚合碎片化的请求以实现加速

如果用常规的方法进行上述操作，必须每次手动地创建vllm实例，再用一定的方式将每个实例分配给请求使用，比如要推理8000条数据，就给每1000条数据分类一个llm engine（本质上就是ddp）。而聚合碎片化请求的功能仅限于请求内部，因为跨请求的上下文是完全无法管理的。

上述问题的出现，本质上是因为**请求llm响应这个过程与llm本身高度耦合**，要获得推理结果必须直接调用一个vllm.LLM或者ModelForCasualLM对象，从而限制了服务端处理请求的灵活性。

于是，一个自然的想法是引入一个**消息队列**作为中间件，实现客户端与服务端的解耦。消息队列将客户端请求缓存下来，服务端可以灵活取用，不仅允许**服务端为全体客户**服务，也天然地提供了**聚合碎片化请求**。常见的中间件还实现了**多消费者模式**，我们也无需手动调度llm，让每个llm都从队列中尝试取一定批量大小消息即可。此外，创建请求过程也变得更加灵活，只要拉起若干llm实例，让他们都监听任务队列，之后随时都可以加入任务以实现请求。*至此，我们就完成了RedisWorker的设计*

而对于客户端而言，原先的向llm发起请求就变成了用一个代理发起请求，代理必须完成两件事：1.将结果发送给服务器。2.获取结果并按序返回。前者只需要将消息放入任务队列即可。对于后者，由于一个请求中包含的多条prompt可能会被分拆到不同的batch中，我们可以为这一整条请求标记一个request_id，根据request_id维护一个消息队列，当服务端完成请求时，就将结果放到request_id对应的队列里。由于服务器不是严格按序返回的，我们还需要给每个prompt分配一个task_id，收集到所有结果后，将其按照传入的顺序排好队返回。*至此，我们就完成了RedisMiddleware的设计*

另外，消息队列还可以帮助我们支持多种LLM共存，例如RewardModel和Inference Model同时提供服务的情况。他们可以使用不同的任务队列，这样就做到了**分别存储，互不干扰**。

接下来基于上述设计选型，坦白来讲我并没有详细了解消息队列的选型比如RabiitMQ。redis作为一个内存数据库而顺带实现了stream功能而迅速进入了我的考虑，在这个场景下我们需要的就是分布式系统中的快速响应性能，并且对持久化没有强烈需求（通常是不会丢请求的），并且启动越简单越好。特别是它还为客户端的中间件提供了不同的选择：Hash/list/stream都可以充当客户端接受结果的方式，虽然实际测试下来list和stream效果都差不多，并且轮询hash也不会太差。

但缺点就是stream不会自动缩容并且一旦fail了就只能从dump文件中恢复了，此外一直看到有说法说redis存大数据会比较慢，大约是10K量级说实话对于长文推理来说达到这个量级的数据还是很轻松的何况，我们需要把token序列化成json，但确实redis用起来太方便了而且感觉**创建消息队列的开销可能会比较小**因为我们每个请求都要创建一个对应的消息队列，当然这是屁股决定脑袋了（我是先决定用redis再想到用hash等存结果的），或许有更好的方法。

总之完成以上选型之后项目也就正式确定，目前实现的RedisMiddleware和RedisWorker不依赖于任何具体的llm服务或者

TODO：
[ ] 现有的服务是json套json，应该改成protobuf之类的
[ ] 请求来回现在都是str，可以再加一个codec层让我们可以指定编码协议，请求响应都用byte，这允许我们自由选择消息编码格式，毕竟对于llm而言传tokenid或许比传字符串省事很多，这需要我们平衡decode的开销和传输的开销。
[ ] 将消息队列本身也抽象出来，只要提供port/host/queuename就可以创建一个队列，当然client相关方法要提供同步和异步两个版本，到时候可能项目就要直接改名了
[ ] 补充更多perf，测试response长短不同时的性能并找出推荐的batchsize，防止极端case下的性能暴跌。

以下是项目的时序图：

![工作流程](https://www.plantuml.com/plantuml/svg/XPBVJzDG5CVV-rSSYGb2fCGV-j04GyAB3wY9u1i9kQmth5b_r7jl4ILBX01MHS209k12n8naSSaM4tKZ6-6VsMst9_y5JzTAYs3Sosvxp_TxpkTxEiu5OSApJEMAo5EBGeuopwJ4LXHX29F2OweRV6HXSlB1o1Hb2vI1R1nrJah1Z-MmybPHvfn5692rBu7V2Alr0GNmvwAbaJDSOWkOC0sAVuGd9uNQEg0eKVRjBwgc2IzC2KQ9XShCquL2DE2UAUuJdX-DIqOd3Iu68bbyzLV3eFHElZcyTDI4Z_3ab2eJY95xsS4qkA62t7hVUxdShJU2RojoOqrkLsLvlzFy2YvpB5T0kByQNyxOrnQ9hiFWaa2BMKCVkyxeKuzoqJ_hH92nuk0G38EjP9fWosGi3Mwg49i_SrY1CkclIxyQl3xklThgRZRariVWfnyirwDEgDNdcUEva7CydFpgqXmHkVmiDrxGuf2IM6RDRwdGEJaDToiGxHg2pKgulIxR7uATpHaxRYBBg_YkqsfXWAb54ZDQtCRvrlVasb7OsWaEIZX7j1ODyfmA-0DReCyXIG2pO7rj18xLqU8qOAx7yzdoR6HmJX1df44KxFyLMUnpQxoZGWmbQnk1l_shlH4UrhgrCiRP_NwSXafo1R3uFDeEAJ4qMYMVjGPtLOYvkwUxyjD1IZz_ENr_rMVKSwRhkBWcOLZKyKGnkKqHeurYbOCpf5KmyUZ9msbyOHT1LTL_VQV38DihijcC5uXyql_SsokdP3ergQ1dSEFKrZtB_0q0)

<!-- ```plantuml
@startuml
!theme cerulean

title Redis-vLLM-Server 工作流程

box "客户端"
    participant Client as Client
end box

box "Redis"
    participant TaskStream as TaskStream
    participant "ResultHash/Stream/List" as Result
end box

box "服务端"
    participant Server as Server
end box

Client -> TaskStream : 1. 发送请求包含多个 prompt 的请求\n(xadd '{task_id=,request_id=,data=}') * n
TaskStream -> Server : 2. 多个worker分别批量获取消息\n(xreadgroup count ${batch_size})
Server -> Server : 3. 使用 vLLM 推理引擎\n批量处理任务
Server -> Result : 4. 写入结果\n(Hash: hset request_key task_id '{result=}')\n(List: rpush request_key '{task_id=, result=}')\n(Stream: xadd request_key '{task_id=, result=}')
Server -> TaskStream : 5. 任务完成，确认消费消息\n(xack msg_id)
Result -> Client: 6. 结果返回\n(Hash: hget request_key task_id) * n\n(List: blpop ${timeout}) * n\n(Stream: xread block ${timeout} COUNT ${n} ...
Client -> TaskStream : * 异常处理，删除消息\n(xdelete msg_id)
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
