"""Annotate the EA1CA889 Perfetto export and write the stage reference table."""
import argparse
import json
from collections import Counter
from pathlib import Path

# Ordered to match StageId in the compiled combine_sst_fp32_profile.cpp.
STAGES = {
    "kernel": ("总段", "单 AIV 的 fused kernel。入口缺点时从首个有效阶段起算", "fused entry", [876, 916]),
    "send_setup": ("初始化 / 搬运", "初始化 UB、路由缓存和通信上下文，写本核供 SO 使用的 magic 源值", "DeviceTransport / fused entry", [878, 882]),
    "send_submit": ("混合总段", "按 chunk 检查 ready，读取路由，批量发布数据 WQE，提交 SO", "Submit", [886]),
    "send_drain": ("完成总段", "按 owner 遍历已用 peer/lane，冲刷待发布队列并回收 CQ", "Drain", [890]),
    "send_wait_local_ready": ("等待 / 搬运", "拉取并检查本地生产者的 magic 和 chunk 水位，直到输入可用", "WaitReady", [173]),
    "send_read_routes": ("元数据读取 / 解析", "读取 peer 的范围、记录数和路由记录，包含 UB 缓存命中路径", "LoadRange / LoadCount / LoadRecord", [114, 135, 145]),
    "send_post_batch": ("Scalar / 描述符搬运", "校验地址和 SQ 容量，为每行 FP32 组装两个 512 B 写的 WQE，将描述符从 UB 写入 SQ，更新队列状态并敲门铃。SQ 空间不足时路径还可等待 CQ", "PostPeerBatch", [326]),
    "send_ring_doorbell": ("控制提交", "写 SQ doorbell，通知 UDMA 获取已发布的 WQE", "UDMARingDoorbell", [394]),
    "send_submit_so": ("通知提交", "向对端 completion 槽提交一次 8 B magic 写，按 peer/lane 记账", "Notify", [233]),
    "send_wait_cq": ("等待 / 回收", "在 Drain 内等待该 QP 达到提交尾部，读 CQE 状态并更新 SQ/CQ tail", "UDMAQuietStatusOnQp", [252]),
    "reduce_setup": ("初始化", "准备归约的 UB 缓冲、通知读取参数和输出视图", "FinalReduceWorker / fused entry", [903, 905]),
    "reduce_total": ("归约总段", "计算本 AIV 的 token 分片，等待归约窗口就绪，归约并写回", "ReduceWindows", [908]),
    "reduce_wait_remote_so_and_local_ready": ("等待 / 搬运", "以 strided MTE2 拉取远端双 lane SO 与本机 gateway ready，轮询仍未匹配 magic 的槽", "WaitWindow", [577]),
    "reduce_rows": ("混合总段", "读 token 的 server mask，按 mask 读取 partial、累加并写回", "ReduceTokens", [628]),
    "reduce_load_partial": ("数据搬运", "将一个远端服务器贡献的 FP32 partial 从 GM 搬到 UB，等 MTE2 完成", "ReduceFp32Token", [733]),
    "reduce_add_partial": ("向量计算 / 同步", "向量 Add 累加 FP32 partial，并同步输入缓冲复用", "ReduceFp32Token", [738]),
    "reduce_write_output": ("数据搬运 / 同步", "准备向量输出，将 token 的结果从 UB 写到 GM，等 MTE3 完成", "ReduceFp32Token", [743]),
    "write_status": ("状态写回", "分别写回本 AIV 的发送和归约状态", "ExportStatus", [900, 913]),
}


def marker_ids(stage):
    index = tuple(STAGES).index(stage)
    return 2 * index + 1, 2 * index + 2


def write_report(summary, destination):
    lines = [
        "# BS4 FP32 发送与归约阶段表", "",
        "本表对应 EP64 / T4 / H256 的打点对象 `ea1ca889`。源码行号对应同目录的 "
        "`combine_sst_fp32_profile.cpp`。Perfetto 时间片的 Arguments 显示 begin 和 end 打点号。", "",
        "耗时是每条 AIV 轨道内同阶段时间片的累计值。中位数和最大值只统计执行过该阶段的轨道。"
        "父阶段含子阶段，同一条轨道上的父子耗时需避免重复计数。核入口打点缺失，核总段以首个有效阶段标记起算。", "",
        "| 阶段 | 打点号 起 / 止 | 动作类型 | 阶段在做什么 | 源码位置 | AIV 数 | 中位 µs | 最大 µs |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for name, (category, description, symbol, lines_in_source) in STAGES.items():
        begin, end = marker_ids(name)
        source = ", ".join(map(str, lines_in_source))
        stats = summary["stages"][name]
        lines.append(f"| `{name}` | {begin} / {end} | {category} | {description} | `{symbol}` · {source}"
                     f" | {stats['tracks']} | {stats['median_us']:.3f} | {stats['max_us']:.3f} |")
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")


def annotate(trace_file, destination):
    trace = json.loads(trace_file.read_bytes())
    finish_by_rank = {}
    counts = Counter()
    for event in trace["traceEvents"]:
        if event.get("ph") != "X":
            continue
        name = event["name"]
        category, description, symbol, lines = STAGES[name]
        begin, end = marker_ids(name)
        args = event.setdefault("args", {})
        args.update(begin_event_id=begin, end_event_id=end, source_file="combine_sst_fp32_profile.cpp",
                    source_lines=", ".join(map(str, lines)), function=symbol, category=category, description=description)
        if name == "kernel":
            args["begin_marker_observed"] = "start" not in args
            rank, owner = event["pid"], event["tid"]
            finish_by_rank[rank] = max(finish_by_rank.get(rank, (-1.0, -1)), (event["ts"] + event["dur"], owner))
        counts[name] += 1
    assert set(counts) == STAGES.keys()
    destination.write_text(json.dumps(trace, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"slices": sum(counts.values()), "stages": len(counts),
            "latest_aiv_by_rank": dict(sorted(Counter(owner for _, owner in finish_by_rank.values()).items()))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    summary = json.loads((args.directory / "profile-summary.json").read_bytes())
    write_report(summary, args.directory / "stage-report.md")
    if not args.summary_only:
        print(json.dumps(annotate(args.directory / "perfetto_trace.json",
                                  args.directory / "perfetto_trace.with-markers.json"), separators=(",", ":")))
