# HT Combine 机间发送 SST 调度对比

测试配置为 EP64、每机 8 rank、每 rank 16 AIV、TopK 6、256 experts、每行 512 B。每个 token 无放回抽取 6 个 expert。每个远端 server 由该 token 命中的首个 rank 作为 gateway，单贡献使用 DIRECT，多贡献使用已准备好的 partial。两组共用 payload、TopK、gateway、owner、lane、返回缓冲区和 completion 槽。

## 对比方式

| 调度 | AIV 处理顺序 |
| --- | --- |
| `rotate` 轮转 | BS32 切成 4 个 8-token chunk。每个 chunk 内按原调度轮流访问 owner 负责的对端 peer |
| `peer` 不轮转 | 每个 owner 按 peer 发完全部 token 的 payload，再换下一个 peer |

两组使用同一个设备对象。参数块偏移 84 的 `SendConfig.reserved` 选择调度。为便于逐字节对照，`peer` 保留 abi 中的四段 range 和相同的 SO 地址，运行时读取覆盖全部 token 的 range。测试开始时预置完整 ready，发送结束时两组均在各自 owner 的所有 payload 之后发 SO，并执行 `Drain`。两组独立运行同一 SST 配置，计时取模拟器打印的完成时刻。每个 seed 的两组先后顺序交替，`20261124` 先跑 `rotate`。

每个用例逐字节核对返回区、32,768 个 completion 槽、1,024 个 AIV status 及其 padding，并检查 256 个 guard；两组核对同一份 golden。配对统计采用两组均验证通过的样本，CSV 保留失败状态。对比采用 `peer − rotate`，负值表示不轮转耗时更低。以 BS32 的 10 对样本为主。若平均配对百分比差距小于 5%，或同一方向少于 8 对，补测 BS64。

## 源码与产物

TileXR 基线固定为 `8882b28a4cc586f91ddcea171170bf9e304adc70`。在隔离源码副本中应用 `sender-sst.patch`，包含以下改动。

- `send_schedule.h` 增加 `SendConfig.reserved=1` 分支，实现不切 chunk 的逐 peer 调度。
- `inter_server_send_kernel.cpp` 与 `device/api.h` 承载两组共享的 SST 适配。使用 MTE 读写 SQ/CQ 状态，使用 32 B 的 GM forward 记录。payload 批按 512 B SQ stripe 发布，每段回收 CQ 后继续，适配历史 SST 取 SQ/CQ 的行为。最终 doorbell 仍用设备写指令，测试结果也包含这些适配成本。
- `kernel.o.gz` 是双方共享的已编译对象，解压后的 SHA-256 记录在 `kernel.sha256`。蓝区 CANN 9.1 独立重建得到相同 SHA-256。

## 复现

在有 TileXR 固定提交的机器上执行下列命令。生成后的每个目录包含 `workload.yml`、`export-layout.json`、64 个 rank 的 `input_bin` 和独立的 golden 目录。

```bash
python3 ht_peer_order/build.py /path/TileXR /path/CANN-9.1.0 ./sender.o.gz
python3 ht_peer_order/reproduce_fixture.py /path/TileXR ./fixture-t32-20261124 --tokens 32 --seed 20261124
```

使用配套 `run.py` 在保留的 SST 模板上执行，用 `status.py` 读取单次运行状态。参数 `tokens` 是 BS，`start` 与 `stop` 指定从 0 起算的 seed 序号区间。现有模板、checker 和导出器由脚本固定，运行使用 8 个 SST 线程。用 `report.py 32 OUTPUT RECEIPT...` 汇总两组 SST 回执。
