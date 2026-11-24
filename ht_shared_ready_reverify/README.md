# 固定提交的 EP64 T4/H128/FP32 SST 复验

`manifest.json` 记录 TileXR 提交 `3bfa9fe5`、Git tree 和各 ELF 的 SHA-256。所有输出使用 `ht-reverify-t4-3bfa9fe5-r1-*` 独立目录。开始时只读加载已校验的机内 Gather 数据。attached、机间回传、同机回传和最终归约均由本次 SST 重新执行。

| 阶段 | 验证范围 |
| --- | --- |
| attached | 直接传入 288 B ABI，设置 `REDUCE_ONLY`，由 kernel 将 FP32 partial 和 8 个 `[int64 magic, int32 epoch, int32 spare]` 槽写入 workspace |
| 机间回传 | 校验 64 rank 共 512 个 ready 槽和 131,072 B partial。生产者导出地址与消费者读入地址相同，校验装载前后的 bytes 和 SHA-256 |
| 同机回传 | 偶数 token 走 self-copy，奇数 token 走同机 fullmesh，校验状态、回传行和 localReady |
| 最终归约 | 修复版同机回传和机间回传均校验通过后，加载这两段的实际输出，逐位校验 32,768 个 FP32 元素 |

蓝区用固定源码运行 `ht_combine_host_contract`，验证 Host 传给各阶段的参数布局和共享地址。绿区通过 raw loader 分段运行 kernel，SST 的耗时按阶段单独记录。绿区对象用 FP32 归约入口，通信对象应用 `sst-transport.patch`，包含 MTE 访问队列状态和 32 B forward stride。固定生产提交的同机回传分配 64 B WQE scratch。修复版按 `d9c5835c` 分配 128 B，与固定提交的实测结果分别记录。

## 复验结果

| 提交 | 阶段 | SST 耗时（µs） | 结果 |
| --- | --- | ---: | --- |
| `3bfa9fe5` | attached 归约 | 26.6901 | 512 个 ready 槽和 32,768 个 FP32 partial 通过 |
| `3bfa9fe5` | 机间回传 | 71.3389 | 7,424 条 CQE，returned、completion 和 status 通过 |
| `3bfa9fe5` | 同机回传 | 11.9988 | 64 个 self owner 成功，448 个非 self owner 返回 `TransportError(3)`，0 条 CQE |
| `d9c5835c` | 同机回传 | 21.0652 | 576 条 CQE，returned、512 个 localReady 和 status 通过 |
| `d9c5835c` | 最终归约 | 17.6134 | 32,768 个 FP32 输出逐位一致 |

逐 rank 核对 attached 导出与两个回传入口的 workspace 地址和实际装载字节。每个 rank 恰好 8 槽，槽首字段为 `(2026, 4, 0)`。FP32 partial 共 131,072 B，ready 共 262,144 B。最终输出 SHA-256 为 `2ab0e9be59309a67f1f80161161237cb8254b87f2cbe8656365796503510057c`。[正式回执](result.json)
