# EP64 T4 shared-ready 联测

沿用已有机内 Gather 的 SST 导出，FP32 行宽 512 B。attached 归约从 Gather 的实际缓冲区读入，写出共享 ready 与 partial。同机和跨机回传均读取这次导出的原始字节。偶数 token 走 self-copy，奇数 token 走同机 fullmesh，每个 token 也走跨机回传。最终归约输入使用两路实际回传的行、SO 和 localReady。

| 独立 SST 阶段 | 模拟耗时（µs） | 校验结果 |
| --- | ---: | --- |
| attached 归约 | 26.6901 | 32,768 个 FP32 partial、512 个 ready、128 个 guard |
| 跨机回传 | 71.3389 | 262,144 个 returned 元素、32,768 个 completion、1,024 个 status、256 个 guard、7,424 条 CQE |
| 同机回传 | 21.0652 | 262,144 个 returned 元素、512 个 localReady、512 个 status、256 个 guard、576 条 CQE |
| 最终归约 | 17.6134 | 32,768 个 FP32 输出逐位一致、1,024 个 status、256 个 guard |

最终输出 SHA-256 为 `2ab0e9be59309a67f1f80161161237cb8254b87f2cbe8656365796503510057c`。各行是独立 SST 运行的耗时。

原同机回传对象只给 WQE scratch 分配了 64 B，`UDMAPostSend` 要求 128 B。64 个 self owner 成功，448 个非 self owner 返回 `TransportError(3)`。收到了 64 个 self localReady，BA trace 无 SQ head、wqeCount、doorbell 写入，也无 SQ fetch 或 CQ 写入。修复版只将 scratch 改为 128 B，复用原 32 B SST record。修复对象解压后的 SHA-256 为 `f74fa81c07b73ed9fa0b9334a99651ccbaa193f26cd2a6c5825ba042ddc279dd`。

[执行脚本](run_returns.py) · [包含输出哈希的正式回执](results/local-scratch128.json) · [原版状态与 BA 计数](results/original-local-evidence.json)
