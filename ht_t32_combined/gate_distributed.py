"""Observe the original T32 sender before starting the distributed control."""
import os


def original_state(case):
    path = case / "run.json"
    try:
        record = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {"status": "receipt_pending"}
    state = {"status": record["status"]}
    log = case / "sim.log"
    if log.is_file():
        stat = log.stat()
        state.update(log_silence_seconds=max(0, int(time.time() - stat.st_mtime)), log_bytes=stat.st_size)
    return state


def enough_to_run_alongside():
    available = re.search(r"(?m)^MemAvailable:\s+(\d+)\s+kB$", Path("/proc/meminfo").read_text())
    return bool(available and int(available[1]) >= 16 * 1024 * 1024 and len(os.sched_getaffinity(0)) >= 16)


def run_distributed(request):
    original = MOUNT / "t32-sst-mte-send-5851c89d"
    last = None
    deadline = time.monotonic() + 1860
    while time.monotonic() < deadline:
        last = original_state(original)
        status = last["status"]
        if status == "sst_and_checker_passed":
            return {"status": "control_not_needed", "original": last}
        terminal_without_result = status in {"timed_out", "interrupted", "failed", "start_failed"}
        quiet_with_resources = (status == "running" and last.get("log_silence_seconds", 0) >= 240
                                and enough_to_run_alongside())
        if terminal_without_result or quiet_with_resources:
            outcome = run_t32(request)
            outcome["original_at_control_start"] = last
            return outcome
        time.sleep(20)
    return {"status": "control_not_started", "original": last}
