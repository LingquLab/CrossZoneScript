"""Select the retained reference and run new cases in a dedicated container."""

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

args = json.loads(sys.argv[1])
build = Path(args["build"])
kind = args["kind"]
size = args["size"]
origin = Path("/home/h30059441/tilexr_sim_cc9_hapi_work")
base = origin / "coord_runner"
generator = "/work/coord_runner/full-matrix-fdcf9d52-20261119/generator"
if kind == "functional":
    request_path = base / "full-finalcq-80cc17ea-20261119/edge-64p-512kib-per-rank-functional.request.json"
else:
    request_path = base / f"full-performance-matrix-80cc17ea-20261119/seed-requests/64p-{size}-performance.json"
request = json.loads(request_path.read_text())
contract = request["prepared_contract"]
assert contract["rank_size"] == 64 and contract["abi_size"] == 128 and contract["block_dim"] == 31
assert contract["device_copyout_executed"] is (kind == "functional")
image = request["image_id"]
assert image == "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
local_image = subprocess.check_output(
    ["docker", "image", "inspect", "--format", "{{.Id}}", image],
    text=True, timeout=15,
).strip()
assert local_image == image
items = json.loads((build / "objects.json").read_text())
assert items["revision"] == "bde4640832397f70516194515f9734ac4c8d8393" and items["passed"]

run = Path(tempfile.mkdtemp(prefix="urma64p-cases.", dir="/opt"))
(run / "prepare.py").write_text(args["prepare"])
name = run.name.replace(".", "-")
print(json.dumps({"stage": "campaign_start", "run_dir": str(run), "schedules": args["schedules"],
                  "kind": kind, "size": size, "deadline": args["deadline"], "revision": items["revision"]}), flush=True)


def command(argv, log, timeout):
    with (run / log).open("wb") as out:
        return subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT, timeout=timeout).returncode


def safe_error(log):
    content = log.read_text(errors="replace")[-12000:]
    patterns = (
        r"(?m)^(?:ValueError|AssertionError|KeyError|FileNotFoundError|RuntimeError|PermissionError|TimeoutError):[^\n]{0,220}",
        r"(?mi)^docker: Error response from daemon:[^\n]{0,200}",
        r"(?mi)^unknown flag:[^\n]{0,200}",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(0)
    return None


started = command([
    "docker", "run", "--rm", "-d", "--name", name,
    "--runtime", "ascend", "--ipc", "shareable", "--shm-size", "64m",
    "--ulimit", "nofile=524288:524288", "--network", "none",
    "-v", f"{origin}:/work:ro", "-v", f"{build}:/candidate:ro", "-v", f"{run}:/run-cases",
    "--entrypoint", "/bin/bash", image, "-c", "sleep infinity",
], "container.log", 40)
if started:
    print(json.dumps({"stage": "container", "exit": started, "error": safe_error(run / "container.log")}))
    raise SystemExit(1)

failed = False
try:
    for schedule in args["schedules"]:
        case_path = f"/run-cases/{schedule}"
        record = dict(
            contract=contract, schedule=schedule, kind=kind, case=case_path,
            deadline=args["deadline"] if schedule == "clock" else 0,
            kernel=f"/candidate/{schedule}/{kind}/{contract['entry']}.o",
            kernel_sha256=items["objects"][f"{schedule}/{kind}/{contract['entry']}.o"],
            revision=items["revision"], generator=generator,
        )
        prefix = ["docker", "exec", "-e", f"PYTHONPATH={generator}", name, "python3", "-B"]
        prep_log = f"{schedule}-prepare.log"
        stage = command(prefix + ["/run-cases/prepare.py", json.dumps(record)], prep_log, 180)
        if stage:
            print(json.dumps({"stage":"prepare","schedule":schedule,"exit":stage,
                              "error":safe_error(run / prep_log),"run_dir":str(run)}), flush=True)
            failed = True
            break
        simulator_log = f"{schedule}-run.log"
        tick = time.monotonic()
        status = command(prefix + [f"{case_path}/case_tools.py", "run", "--case-dir", case_path,
                                  "--threads", "32", "--timeout", str(args["timeout"])],
                         simulator_log, args["timeout"] + 90)
        result = dict(stage="sst", schedule=schedule, kind=kind, size=size, exit=status,
                      wall_seconds=round(time.monotonic()-tick, 3), run_dir=str(run), deadline=record["deadline"])
        case = run / schedule
        for path in sorted(case.glob("runs/*/run.json"))[-1:]:
            report = json.loads(path.read_text())
            result["run_status"] = report.get("status")
            result["report_keys"] = sorted(report)
        sim_logs = sorted(case.glob("runs/*/sim.log"))
        elapsed_pattern = re.compile(rb"Simulated time:\s*(\d+(?:\.\d+)?)\s*(ps|ns|us|ms|s)\b", re.I)
        if sim_logs:
            with sim_logs[-1].open("rb") as stream:
                stream.seek(max(0, sim_logs[-1].stat().st_size - 65536))
                matches = elapsed_pattern.findall(stream.read())
            if matches:
                number, unit = matches[-1]
                result["simulated_us"] = float(number) * {
                    b"ps": 1e-6, b"ns": 1e-3, b"us": 1, b"ms": 1000, b"s": 1000000,
                }[unit.lower()]
        if status:
            result["error"] = safe_error(run / simulator_log)
        (case / "candidate-result.json").write_text(json.dumps(result) + "\n")
        print(json.dumps(result), flush=True)
        failed |= status != 0
finally:
    cleanup = command(["docker", "stop", "-t", "10", name], "cleanup.log", 35)
    print(json.dumps({"stage":"cleanup","exit":cleanup,"run_dir":str(run)}), flush=True)
raise SystemExit(1 if failed or cleanup else 0)
