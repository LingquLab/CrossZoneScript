from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import re
import runpy
import sys

from controller import HISTORICAL, OUTPUT, REMOTE, RUNNER
from key_transport import install
from run_matrix import RESULT


def observe(directory):
    from collections import Counter
    from pathlib import Path
    import json
    import re

    def tail(path):
        if not path.is_file():
            return ""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 16384))
            return stream.read().decode(errors="replace")

    run = Path(directory)
    children_path = run / "children.json"
    children = json.loads(children_path.read_text()) if children_path.is_file() else []
    states = Counter()
    failures = Counter()
    barriers = Counter()
    rounds = Counter()
    for child in children:
        rank = child["rank"]
        proc = Path("/proc") / str(child["pid"]) / "stat"
        try:
            state = proc.read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            state = "exited"
        states[state] += 1
        rankdir = run / f"rank-{rank}"
        rounds[len(list(rankdir.glob("*.summary.json")))] += 1
        logs = tail(rankdir / "stdout.log") + "\n" + tail(rankdir / "stderr.log")
        matches = re.findall(r"UBX_DEVICE_BARRIER[^\n]* round=(\d+)", logs)
        barriers[max(map(int, matches)) if matches else -1] += 1
        for message in ("UBX_FAILURE", "Synchronize", "timeout", "Connection refused", "Address already in use",
                        "ERROR", "AICore exception", "insufficient", "msprof"):
            if message.casefold() in logs.casefold():
                failures[message] += 1
    result_path = run / "run-result.json"
    result = json.loads(result_path.read_text()) if result_path.is_file() else {}
    return {"process_states": states, "rounds_per_rank": rounds, "last_barrier_round": barriers,
            "markers": failures, "result": result.get("status"), "network_restored": result.get("network_restored"),
            "interference": bool(result.get("interference"))}


def main(case):
    import inspect

    assert re.fullmatch(r"bs(?:32|64|128)-(?:baseline|candidate)-(?:off|on)", case)
    outcome = RESULT / case
    profile = json.loads((outcome / "profile.private.json").read_text(encoding="utf-8"))
    identity = json.loads((outcome / "run-identity.json").read_text(encoding="utf-8"))
    run_id = identity["run_id"]
    assert re.fullmatch(r"topk-" + case + r"-[a-f0-9]{10}", run_id)
    spec = importlib.util.spec_from_file_location("ep32_runner", RUNNER)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    helper = HISTORICAL / "ubx-task-rsa-key-transport.py"
    if helper.is_file():
        assert runner.digest(helper) == "847b4a4801b6ddc63315aacbb092f4c0336241cb276bd96c1f954284276d129a"
        runpy.run_path(str(helper))["install"](runner)
    transport = install(runner, profile, runner.transport if helper.is_file() else None)
    directory = f"{REMOTE}/runs/{run_id}"
    script = "import json\n" + inspect.getsource(observe) + f"\nprint(json.dumps(observe({directory!r})))\n"

    def run(node):
        reply = transport(profile, node, script, outcome, "observe", 20)
        return {"node": node, **reply}

    with ThreadPoolExecutor(max_workers=2) as pool:
        print(json.dumps({"run_id": run_id, "hosts": list(pool.map(run, (0, 1)))}, separators=(",", ":")))


if __name__ == "__main__":
    assert sys.argv[1] == "--args-file"
    main(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["case"])
