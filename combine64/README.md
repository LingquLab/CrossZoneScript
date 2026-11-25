# combine-ht bs32 SST 联合验证

`64p-t32-pipeline-a3` 在一次 SST 运行中通过了完整联合验证。配置为 64 rank，每个 rank 32 个 token，hidden 128，topk 6，256 个 expert，FP32。验证环境为 CANN 9.1.0、dav-c310-vec（ARCH3510）和 SST 8 线程。

| 版本 | GitHub 提交 |
| --- | --- |
| TileXR 源码基线 | [`40596e2d9d454b7a7b00b4b34b96d49158e1c3a3`](https://github.com/LingquLab/TileXR/commit/40596e2d9d454b7a7b00b4b34b96d49158e1c3a3) |
| 通过验证的仿真构建与运行脚本 | [`fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b`](https://github.com/LingquLab/CrossZoneScript/commit/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b) |

仿真脚本在 TileXR 基线之上生成隔离构建，变体为 `four-module-sq-align-all512-cq-strided-seeded`。SST 实际加载的内核文件 SHA-256 为 `297ae429748b21f4ffddf1abc836d485dc5a0e7eb95c60d84a2872046fb96c11`。

## 构建改动

- 机内和机间发送在 SQ 的 512 字节边界处分段发布 WQE，跨界前等待已发布请求完成。[机内发送构建脚本](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/sq_align_build.py) · [机间发送构建脚本](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/inter_sq_align_build.py)
- SQ 修正后的联合构建包含 CQ DCCI 前后的编译器内存屏障。联合同步由 leader 用跨步搬运读取 16 条到达标记，每条读取 4 字节。[CQ 屏障构建脚本](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/cq_compiler_barrier.py) · [联合构建脚本](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/cq_visible_joint_build.py)
- FP32 最终规约用首条有效输入初始化每个 token 的累加器，随后按原顺序累加其余输入。没有有效输入时写零。[最终规约修正](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/cq_visible_joint_build.py#L23)

## 运行入口

绿区 EXEC 使用上述 CrossZoneScript 固定提交和入口 `combine64/green.py`，参数为 `{"action":"seeded-final-joint32","tokens":32}`。SST 限时 1,500 秒，EXEC 限时 2,300 秒。入口只在已准备的绿区仿真工作区运行。[执行脚本](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/green.py)

构建脚本校验已保存的机内和机间 SQ 修正、CQ 屏障版本、跨步同步构建，以及 `64p-t32-pipeline-a2` 的校验失败记录与内核哈希。运行阶段使用该工作区已安装的 bs32 输入和 ABI，再构建并运行 `64p-t32-pipeline-a3`。[工作区检查和运行参数](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/host.py#L373) · [bs32 输入标识](https://github.com/LingquLab/CrossZoneScript/blob/fc13cb2c48f486de0e9bf55f93ba1f9c719c3a2b/combine64/data/64p-t32/identity.json)

## 结果

| 项目 | 结果 |
| --- | --- |
| SST 和数值校验 | 通过，退出码 0 |
| 保护区检查 | 640 处全部通过 |
| Profiling | 1,024 条 lane 记录全部收齐，丢失 0 条 |
| 平台模拟耗时 | 287.071 µs |
| 算子 profiling 耗时 | 282.059 µs |
| SST 实耗 | 1,022.495 s |
| Runner 实耗（含完整性检查、导出和数值校验） | 1,095.218 s |

验证回执为 `green-0b2ca64156bb4297b88a11d6827bf516`。bs64 的 `64p-t64-pipeline-a2` 加载了相同哈希的内核，运行中按用户要求取消。
