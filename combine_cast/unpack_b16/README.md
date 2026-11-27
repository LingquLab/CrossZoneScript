# SST 单 AIV 复现 `DIST_UNPACK_B16` 加载异常

在加载了 Cast 修复库的 V310 SST 上，`DIST_UNPACK_B16` 返回与普通加载相同的寄存器布局。相同的 ELF 在 CANN 9.1 A5 功能模型上正确解包。功能模型指令日志将两次解包解码为 `RV_VLDI dist:UNPK_B16`，指令字均为 `0x00080398`。目前定位到两套模型在执行这个加载模式时的语义差异。SST 内部触发差异的代码路径尚未确认。此用例对应 HT Combine 的 FinalReduce。[数值、指令和正式回执](../results/v310-unpack-b16-minimal.json)

## 输入与判定

[探针源码](probe.cpp)只启动一个 chip、一个 AIV，输入为 128 个不同的连续 16 位值 `0x3f80…0x3fff`。三次向量加载使用同一 UB 地址和相同的 32 位原样存储。每组检查 64 个 32 位槽，探针中没有 Cast、累加或通信。

| 加载 | 预期前两个槽 | SST 实测前两个槽 | SST 错槽 | A5 功能模型错槽 |
|---|---|---|---:|---:|
| BF16 `DIST_NORM` | `3f813f80 3f833f82` | `3f813f80 3f833f82` | 0/64 | 0/64 |
| BF16 `DIST_UNPACK_B16` | `00003f80 00003f81` | `3f813f80 3f833f82` | 64/64 | 0/64 |
| UINT16 `DIST_UNPACK_B16` | `00003f80 00003f81` | `3f813f80 3f833f82` | 64/64 | 0/64 |

FinalReduce 随后的 Cast 使用 `RegLayout::ZERO`，从每个 32 位槽的低 16 位取值。按异常布局读取时，前两个输入变成 `0x3f80、0x3f82`，原本的第二列 `0x3f81` 被跳过。[CANN 9.1 LoadAlign](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/basic_api/reg_vector_compute/reg_data_load/LoadAlign_continuous.md) · [CANN 9.1 RegLayout](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/910/API/ascendcopapi/docs/en/api/SIMD-API/basic_api/reg_vector_compute/aux_data_types/RegLayout.md)

## 在现有绿区环境复跑

在本仓库根目录使用已配置的绿区执行环境。通过 CrossZoneScript 调度时，入口为 `combine_cast/unpack_b16/run.py`，参数为 `{"label":"unpack-b16-repro"}`。每次使用新的 label，避免覆盖历史目录。入口自带预编译探针，沿用 Cast 修复镜像和现有单芯片 SST 平台夹具，并自动生成输入、启动 SST 和校验。

也可在同一个绿区环境直接执行以下命令。

```bash
printf '%s\n' '{"label":"unpack-b16-repro"}' > /tmp/unpack-b16-args.json
python3 combine_cast/unpack_b16/run.py --args-file /tmp/unpack-b16-args.json
```

`sst_exit=0` 表示仿真完成。`check_exit=1` 且两组解包均为 `wrong_lanes=64, packed_lanes=64` 表示复现了故障。`guards_ok=true` 表示两个边界哨兵通过。修复后应得到 `check_exit=0`，三组各 64 个槽逐位正确。

归档的运行目录由回执中的 `artifact_dir` 给出。目录内有 `probe.o`、`workload.yml`、`chip0/input.bin`、`sim.log`、`check.log`、两个 Die 的原始 dump 和 `actual-raw.bin`。用例将输出导出范围对齐到完整的 512 B 记录，checker 从两个 Die 重建 1,536 B 并检查其中的结果和哨兵。

需要重编译时，可传入已有的 CANN 9.1 BF16 Combine CMake 构建目录，脚本取其 `dav-c310-vec` 编译和链接参数。

```bash
python3 combine_cast/unpack_b16/build.py <combine-build-dir>
```
