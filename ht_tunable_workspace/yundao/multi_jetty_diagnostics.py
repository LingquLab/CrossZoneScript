"""Read retained failures and qualify the host before a targeted Gather retry."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
os.environ["TILEXR_VALIDATION_RUN_LABEL"] = "ht-4d37ae67-ep8-diagnostic"
import remote
from matrix import run_group

ROOT = remote.root
ORIGINAL = ROOT.parent / "ht-4d37ae67-multi-jetty-ep8-1795859849"
REVISION = "4d37ae67cf2f6baa06b3efbe65c71649a6f24203"
RESULT = {"revision": REVISION, "log_dir": str(ROOT)}


def log_evidence():
    evidence = {}
    for folder in sorted(ORIGINAL.iterdir()):
        if not (folder.is_dir() and folder.name.startswith(("return-", "gather-w64-"))):
            continue
        counts = []
        marker_counts = Counter()
        for log in sorted(folder.glob("rank-*.log")):
            text = log.read_text(errors="replace")
            counts.append(text.count("correctness=pass"))
            for tag in ("LAUNCH_READY", "ROUND_READY", "507035", "ready", "communicator"):
                marker_counts[tag] += bool(re.search(re.escape(tag), text, re.I))
        evidence[folder.name] = {"ranks": len(counts), "completed_rounds": sum(counts),
                                 "markers_by_rank": dict(marker_counts)}
    flags = Counter()
    earliest = min(path.stat().st_mtime for path in ORIGINAL.glob("return-*/rank-*.log"))
    latest = max(path.stat().st_mtime for path in ORIGINAL.glob("return-*/rank-*.log"))
    locations = (Path.home() / "ascend/log/debug/plog", Path.home() / "ascend/log/run/plog",
                 Path.home() / "ascend/log/plog")
    tokens = {
        "507035": r"507035", "local_return_kernel": r"tilexr_ep_ht_local_return_kernel",
        "vector_exception": r"vec(?:tor)? (?:core )?exception", "mte_exception": r"mte.*(?:error|exception)",
        "pc_out_of_range": r"pc out of range", "timeout": r"aicore.*timeout|task timeout",
        "ub_error": r"ub address|ub.*out of bounds|ub.*overflow",
        "illegal_instruction": r"illegal instruction|illegal instr", "ddr_error": r"ddr address|ddr.*overflow",
        "aic_error": r"aic(?:ore)?(?: |_)error",
    }
    for location in locations:
        recent = [(path.stat().st_mtime, path) for path in location.glob("plog-*.log") if path.is_file()]
        for stamp, path in sorted(recent, reverse=True)[:32]:
            if earliest - 45 <= stamp <= latest + 180:
                with path.open("rb") as handle:
                    handle.seek(max(0, path.stat().st_size - 1024 * 1024))
                    lines = handle.read().decode(errors="replace").splitlines()
                for label, pattern in tokens.items():
                    flags[label] += sum(bool(re.search(pattern, line, re.I)) for line in lines)
    RESULT["retained_cases"] = evidence
    RESULT["plog_markers"] = {key: count for key, count in flags.items() if count}


def qualify():
    retained = sorted(ROOT.parent.glob("*/hccl-result.json"), reverse=True)
    for report in retained:
        data = json.loads(report.read_text())
        if data.get("mode") == "aiv_only" and data.get("status") == "pass":
            return data
    completed = subprocess.run([sys.executable, str(HERE / "hccl_baseline.py")], capture_output=True, timeout=440)
    rows = [line[len("TILEXR_RESULT "):] for line in completed.stdout.decode(errors="replace").splitlines()
            if line.startswith("TILEXR_RESULT ")]
    return json.loads(rows[-1]) if rows else {"status": "qualification_runner_failed", "exit": completed.returncode}


def test():
    source = json.loads((ORIGINAL / "result.json").read_text())
    if source.get("revision") != REVISION or source.get("native_build") != "pass":
        raise ValueError("invalid run provenance")
    log_evidence()
    qualification = qualify()
    RESULT["hccl"] = qualification
    if qualification.get("status") != "pass":
        return
    rootinfo = remote.write_eid_rootinfo()
    env = dict(os.environ, TILEXR_UDMA_ROOTINFO_PATH=str(rootinfo), TILEXR_UDMA_SHARED_QP_SIX_PORT_COUNT="16")
    executable = str(ORIGINAL / "build/src/moonep/combine_ht/tilexr_combine_ht_test")
    retries = RESULT["gather_retries"] = {}
    for mode, total in [("fused", 32), ("fused", 39), ("send", 39)]:
        name = f"w64-t32-a8-{mode}-q{total}"
        def args(rank):
            return [executable, "--mode", mode, "--world", "64", "--rank", str(rank), "--device", str(rank),
                    "--local-comm", "1", "--tokens", "32", "--hidden", "128", "--topk", "4", "--chunk", "32",
                    "--splits", "4", "--reduce-begin", "8", "--intra-cores", "8", "--reducers", "16",
                    "--iterations", "1", "--domain", "0"]
        logs, row = run_group("gather-" + name, args, range(8),
                             dict(env, TILEXR_UDMA_SHARED_QP_COUNT=str(total)), 60)
        retries[name] = row
        if logs:
            row["checked_ranks"] = sum("correctness=pass" in log for log in logs)
            if row["status"] == "pass" and row["checked_ranks"] != 8:
                row["status"] = "missing_checks"
        if row["status"] == "devices_busy":
            break


try:
    test()
except (OSError, ValueError, subprocess.SubprocessError) as error:
    RESULT["error_type"] = type(error).__name__
(ROOT / "result.json").write_text(json.dumps(RESULT, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(RESULT, ensure_ascii=True, separators=(",", ":")))
