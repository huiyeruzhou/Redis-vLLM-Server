import ray
from omegaconf import DictConfig, OmegaConf

from redis_util.redis_worker import RedisServer

from model import VLLMModel


def get_gpu_count():
    nodes = ray.nodes()
    gpu_count = 0
    for node in nodes:
        resources = node.get("Resources", {})
        gpu_count += resources.get("GPU", 0)
    return gpu_count

if __name__ == "__main__":
    import hydra
    from omegaconf import DictConfig, OmegaConf

    @hydra.main(config_path="conf", config_name="inference_server", version_base=None)
    def main(cfg: DictConfig):
        OmegaConf.resolve(cfg)
        print("Configuration:")
        print(OmegaConf.to_yaml(cfg))
        import os
        print(os.getcwd())
        ray.init(address="auto", ignore_reinit_error=True,runtime_env={"working_dir": os.getcwd()})
        
        # 创建 RedisServer 实例，设置优雅退出处理函数
        redis_server = RedisServer(cfg)
        import signal
        signal.signal(signal.SIGINT, redis_server.exit_handler)

        # 启动 若干RedisWorker
        num_workers = cfg.worker.num_gpus // cfg.llm.tensor_parallel_size
        if cfg.worker.num_gpus % cfg.llm.tensor_parallel_size!= 0:
            Warning(f"Number of GPUs should be divisible by tensor_parallel_size, {cfg.worker.num_gpus % cfg.llm.tensor_parallel_size=} left")
        if cfg.worker.num_gpus is not None and cfg.worker.num_gpus != get_gpu_count():
            Warning(f"Number of GPUs in config ({cfg.worker.num_gpus}) does not match the number of available GPUs ({get_gpu_count()})")
        redis_server.run_workers(num_workers, VLLMModel)

    main()