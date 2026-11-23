"""Read the fused SST run and analyze its BA traces after a timeout."""
from collections import Counter
from datetime import datetime, timezone

ROOT = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
CASE = ROOT / 't32-combine-fp32-c4297a51'
CONTAINER = 'tilexr-cpu-b4f4660a-ht-t32-e2e-c4297a51'


def model_log(path):
    result = {"bytes": path.stat().st_size, "silence_seconds": max(0, int(time.time() - path.stat().st_mtime)),
              "cqe_records": 0, "errors": {}}
    errors = Counter()
    error_types = {
        b"segmentation fault": "segfault", b"fatal error": "fatal_error", b"assertion failed": "assertion_failed",
        b"out of memory": "out_of_memory", b"mpi_abort": "mpi_abort", b"traceback (most recent call last)": "traceback",
    }
    clock = re.compile(rb"(?i)\b(Sim|simTime|cycle|timestamp|tick|time)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\s*(ps|ns|us|ms)?")
    completion = re.compile(rb"Simulation is complete, simulated time: ([0-9.]+) (ps|ns|us|ms|s)")
    with path.open("rb") as stream:
        for line in stream:
            if b"WrCQEResp" in line:
                result["cqe_records"] += line.count(b"WrCQEResp")
                matches = clock.findall(line)
                if matches:
                    field, value, unit = matches[-1]
                    result["last_cqe_time"] = {"field": field.decode(), "value": value.decode(), "unit": unit.decode() or None}
            marker = completion.search(line)
            if marker:
                result["completion"] = [part.decode() for part in marker.groups()]
            lower = line.lower()
            for pattern, tag in error_types.items():
                if pattern in lower:
                    errors[tag] += 1
    result["errors"] = dict(errors)
    result.setdefault("last_cqe_time", None)
    result.setdefault("completion", None)
    return result


def docker_state():
    proc = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", CONTAINER],
        capture_output=True, timeout=8,
    )
    if proc.returncode:
        return {"inspect_exit": proc.returncode}
    state = json.loads(proc.stdout)
    result = {"status": state["Status"], "oom_killed": state["OOMKilled"], "exit_code": state["ExitCode"],
              "started_at": state["StartedAt"]}
    try:
        start = datetime.fromisoformat(state["StartedAt"].replace("Z", "+00:00"))
        stop = datetime.now(timezone.utc) if state["Running"] else datetime.fromisoformat(
            state["FinishedAt"].replace("Z", "+00:00"))
        if start.year > 1 and stop >= start:
            result["runtime_seconds"] = round((stop - start).total_seconds(), 1)
    except ValueError:
        pass
    return result


def inspect_transport():
    totals = Counter()
    stripes = {kind: {"expected": Counter(), "missing": Counter()} for kind in ("sq", "cq")}
    missing_examples = {}
    expected_all = sum(sum(rings.values()) for rings in RINGS.values())
    for host, rings in RINGS.items():
        path = CASE / "OUTPUT" / "stat_trace" / f"Host{host}_ba_layer_trace.json"
        if not path.is_file():
            continue
        observed, truncated = read_observed(path, host)
        totals["hosts_examined"] += 1
        totals["trace_prefix_truncated"] += int(truncated)
        for ring, count in rings.items():
            category = "payload_qp" if count > 1 else "so_only_qp"
            totals[category + "_expected"] += count
            for role, event in (("sq", "RD_REQ"), ("cq", "WR_REQ")):
                slots = observed[event].get(ring, set())
                missing = sorted(set(range(count)) - slots)
                totals[category + "_" + role + "_observed"] += count - len(missing)
                if category == "payload_qp":
                    stripes[role]["expected"].update(slot // 8 for slot in range(count))
                    stripes[role]["missing"].update(slot // 8 for slot in missing)
                    if missing:
                        missing_examples.setdefault(role, {"host": host, "ring": ring, "slots": missing[:8]})
    return dict(expected_slots_all_hosts=expected_all, totals=dict(totals), payload_stripes=stripes,
                first_missing=missing_examples)


def check():
    result = {"case_exists": CASE.is_dir()}
    state = {}
    for name in ("run.json", "task-result.json", "e2e-summary.json"):
        path = CASE / name
        if path.is_file():
            try:
                record = json.loads(path.read_text())
            except ValueError:
                continue
            if name == "run.json":
                state = record
            keys = ("status", "returncode", "timeout_seconds", "guards", "run", "executed_sst",
                    "sst_simulated_microseconds", "checked", "first_mismatch", "runner_exit_code",
                    "private_container_removed", "cqe_log_records")
            result[name] = {key: record[key] for key in keys if key in record}
    result["container"] = docker_state()
    if (CASE / "sim.log").is_file():
        result["log"] = model_log(CASE / "sim.log")
    if state.get("status") == "timed_out":
        result["ba"] = inspect_transport()
    return result
