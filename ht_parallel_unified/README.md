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
