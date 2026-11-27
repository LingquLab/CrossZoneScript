# 单 rank FinalReduce 的 EP64 对照

SST 只启动 rank 0。配置保留 EP64 的 64 个逻辑 rank 和 8 个 server。每个 token 的 server mask 为 `0xff`，workspace 中有 8 行不同的 FP32 partial。每轮固定 H128、16 AIV，关闭 Profiling。数据和完成标记在启动前全部预置。

| 模式 | 执行路径 |
|---|---|
| 整批归约 | 按整批 token 给 16 个 AIV 分工，直接进入 `ReduceTokens` |
| Window | 用生产调度 `ReduceWindows` 在每个 Window 检查就绪标记，再分配该段 token |
| PhysicalChunk | 同一调度器按物理 chunk 检查就绪标记，再分配该段 token |

三种模式使用同一个 ELF、同一 worker 和同一份 partial。计时取独立 SST workload 的 `sim_us`。结果反映数据已就绪时的归约、就绪检查和分段开销。吞吐量按每 token 读取 8×128×4 B、写回 128×4 B 计算。每组比对全部输出的 FP32 位模式和 16 个 AIV 状态，以及输出缓冲区前后哨兵。

| 每 rank token | 整批／Window／PhysicalChunk 检查段数 | 有效 partial 字节 |
|---:|---:|---:|
| 32 | 0／1／4 | 131,072 |
| 128 | 0／1／4 | 524,288 |
| 512 | 0／2／5 | 2,097,152 |

这里所有标记均已就绪。逐 chunk 提前启动可以节省多少通信等待，需要用带实际生产者的时间线验证。

在现有绿区环境从仓库根目录运行 `python3 ht_tunable_workspace/final_reduce_bench/run.py --args-file <args.json>`。参数示例为 `{"label":"final-reduce-bench-repro","tokens":[32,128,512]}`，每次使用新 label。结果明细和仿真日志保存在绿区 case 目录。重编译可运行 `python3 ht_tunable_workspace/final_reduce_bench/build.py <TileXR-源码目录> <CANN-构建目录>`。
