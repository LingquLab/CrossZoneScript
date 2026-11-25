# EP64 HT Combine 单次 SST 验证

TileXR 源码固定为 `4d9a6f2f62f0b343998ea9760637cf3993e0539c`。算子入口为 `tilexr_ep_ht_combine_parallel_kernel`，对象解压后的 SHA-256 为 `1abdfffda622579ec3327f092a3a3960ba9959c303b1f6653f1c9691c297ba28`。64 rank 各运行 BS4/H128 FP32，单次调用启动 56 AIV。Gather、partial、ready、localReady 和 completion 从空缓冲区开始。

| 阶段 | AIV 编号 | 64 rank 的 status 结果 |
| --- | --- | ---: |
| 机内发送 | 0–7 | 512 个为 0 |
| Gateway 归约 | 8–15 | 512 个为 0 |
| 同机回传 | 16–23 | 512 个为 0 |
| 跨机回传 | 24–39 | 1,024 个为 0 |
| 最终归约 | 40–55 | 1,024 个为 0 |

SST 模拟耗时 **109.405 µs**。SST 和 checker 均退出 0。9 个区域合计逐字节校验 20,938,752 B，其中 32,768 个 FP32 最终值全部通过，256 个 guard 全部通过。日志包含 12,364 条 `WrCQEResp` 记录。脚本回传首次退出 1，随后从保留的运行文件与校验摘要取得完整结果。[运行状态](results/terminal.json) · [校验计数](results/overlap.json)

## Trace 覆盖

跨机 token 0 到达 rank `R` 时，发送端为 `R ^ 8`。64 份 BA trace 匹配到 256 条该到达事务的 request、data 和 response 事件。BA 未匹配到同机 token 0 self-copy、发送端首条 SQ 写、token 1–3 的 local/cross partial 写、ready 写或最终输出写。64 份 DL trace 共有 96,319 条事件，其中 93,759 条带时间戳。DL 包含 `aicoreId_`、`oriAddr_` 和 `wrAddr_` 字段，未匹配到 partial/ready 目标地址的 AIV MTE3 写入。因缺少生产端的写入时刻，本轮无法确认 chunk 0 回传和后续 chunk 生产重叠，也无法确定 final 的计算重叠。[BA 配对结果](results/overlap.json) · [DL 字段与事件统计](results/dl.json)

## BS32 同一 ELF 联测

EP64/H128 FP32，单次入口启动 56 个 AIV。每 rank 处理 32 个 token，按 4 个 8-token chunk 运行，共用一个完成窗口。64 rank 输入使用相同的同机 self-copy、同机邻居和跨机 `rank ^ 8` 路由。夹具预期校验 262,144 个 FP32 最终值、3,584 个 AIV status 和 256 个 guard。

| 运行 | SST 墙钟上限 | SST 终态 | 最终校验 |
| --- | ---: | --- | --- |
| 首轮 | 2,400 s | 超时 | checker 未启动 |
| 复测 | 4,800 s | 超时 | checker 未启动 |

绿区核对了两轮的 ELF、1,920 个输入文件、577 个 golden 和布局文件的 SHA-256，以及归一化目录名后的 workload。两轮均未得到完成的模拟耗时。复测摘要中的 `sim_us: 0.0` 随超时状态出现，不能用作性能数据。

| 阶段 | 模型预期 CQ 槽 | BA 观察到的 CQ 写入槽 |
| --- | ---: | ---: |
| Gather | 16,128 | 16,128 |
| 同机回传 | 1,472 | 1,472 |
| 跨机回传 | 9,216 | 8,831 |
| 合计 | 26,816 | 26,431 |

首轮和复测终态的 CQ 槽缺口一致。64 个 rank 发往 `rank ^ 8` 的 lane 0 全部缺最后的窗口 SO CQE。63 条 QP 缺 BB19–24，rank 5 发往 rank 13 的 QP 缺 BB18–24。64 条 QP 合计记录 320 次 doorbell 写请求和 319 个 SQ fetch 起点。BA 未捕获这些 QP 的本地 SQ head 写入。接收侧匹配到 7,167／7,168 个跨机 SO 槽的 `WR_DATA`。复测终态日志有 27,199 条 `WrCQEResp` 字样，实际 CQ 槽仍缺 385 条。[首轮与复测回执](results/bs32)

BS32 计划由 `prepare_blue.py --tokens 32` 配合原 TileXR 与兼容源生成。绿区首轮和复测分别用 `run.py` 的 `tokens=32` 和 `tokens=32,long_wall=true`，目录名分别为 `ht-parallel-ep64-t32-fp32-1abdfffd` 与同名加 `-long-wall`。

## BS16 同一 ELF 联测

将每 rank 的 token 数降至 16，保留 4 个实际 chunk，每个 chunk 含 4 个 token。EP64/H128 FP32、56 个 AIV 以及同机和跨机路由沿用 BS32 的设置。使用原 ELF，墙钟上限 4,800 秒。

SST 达到墙钟上限，进程退出码为 `-9`。最后一条 `WrCQEResp` 的模拟时刻为 61.644 µs，日志共记录 7,846 条。checker 未启动。预期校验的 131,072 个 FP32 输出、3,584 个 AIV status 和 256 个 guard 均未获得终态校验。

| 阶段 | 预期 CQ 槽 | 终态 BA trace 匹配到的 CQ 槽 |
| --- | ---: | ---: |
| Gather | 8,960 | 7,346 |
| 同机回传 | 960 | 0 |
| 跨机回传 | 8,192 | 550 |

448 条 Gather QP 各记录 8 次 doorbell 写请求，全部缺最后一个 chunk 的 Gather SO CQE。终态 CQ 槽统计与先前快照一致。[BS16 终态和队列回执](results/bs16)

## BS8 原版对照

保留原 ELF，改为每 rank 8 个 token，每个 chunk 含 2 个 token。SST 达到 2,400 秒墙钟上限，checker 未启动。最终 BA trace 覆盖全部 64 个 rank。Gather 匹配 5,332／5,376 个 CQ 槽，同机回传匹配 451／704 个，跨机回传匹配 5,012／7,680 个。更小的 BS 仍会出现 CQ 缺失。[BS8 终态和队列回执](results/bs8)

## BS32 跨机 SQ 修正

原版从 rank 5 发往 rank 13 的 SQ 记录了一次从 BB18 开始、长 448 B 的取回响应，跨过 BB23 之后的 512 B 边界，把 payload 和窗口 SO 放入同一次取回。作为对照，rank 0 发往 rank 8 的 SQ 从 BB18 取回 384 B，然后从 BB24 另取 64 B。两条 QP 都缺跨机 CQ 槽。[跨机 SQ 定位回执](results/bs32/retry-tail-sq-fetch.json)

SST 兼容对象把跨机 WQE 按各自所在的 512 B SQ 区段分批，推进到下一区段前，先回收前一区段的 CQ。随后使用原 BS32 夹具、8 个模拟线程和 4,800 秒墙钟上限复测。绿区逐文件核对了 1,920 个输入和 577 个 golden 与布局文件，并确认归一化 workload 一致。[兼容改动](cross-stripe-sst.patch) · [输入核对回执](results/bs32-cross-stripe/matched-inputs.json)

第一版修正也达到 4,800 秒墙钟上限，checker 未启动。停止推进时，Gather 与同机回传各自收齐 16,128 和 1,472 个 CQ 槽，跨机从原版的 8,831 增至 9,024 个，距预期仍少 192 个。缺失分布在 lane 0 的 56 条 QP 和 lane 1 的 136 条 QP，每条各缺一个 CQ 槽。192 条 QP 均可匹配到 doorbell 写请求，其中 88 条只发窗口 SO 的 QP 无对应 SQ fetch。[终态](results/bs32-cross-stripe/terminal.json) · [队列明细](results/bs32-cross-stripe/cross-missing.json)

BS8 可通过 `run.py` 的 `tokens=8` 参数复现。BS32 修正版通过 `tokens=32,cross_stripe=true` 运行，ELF 解压后 SHA-256 为 `99de129d1b048def25f006e84c99b66b116fd14064164da72d1a44b71f9224e9`。

第二版保留 512 B SQ 分段，在每批 payload 和每条 SO 发布后按对应 QP 的实际完成数回收 CQ，再继续发布。调用参数为 `tokens=32,cross_stripe="ordered"`，ELF 解压后 SHA-256 为 `88733694f123a5156aade70429597728d2e003db51dfef19efbda792fedbd8f5`。[逐批回收改动](cq-ordered-sst.patch)
