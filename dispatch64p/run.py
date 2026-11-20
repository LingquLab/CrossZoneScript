from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


CASE = Path("/opt/sim/simulator/Testcases/dispatch_tilelang_64p_top6_68d32608_direct01")
ELF_SHA = "6d3107745c9f0997abc107c1eab5f36cfc278ce1fd228a09a151137eb1e69a37"
WORKLOAD_SHA = "db0c166a1391e1cfd457605fef4a8aa2b288a9df72e248638427a77a4119bcbb"
AIV_AFFINITY = ",".join(str(cpu) for cpu in range(0, 80, 2))
RECEIPT = CASE / "direct-run-receipt.json"
COMMAND = (
    "source /opt/sim/sst-env.sh && "
    "export SST_PATH=/opt/sim/local/sstcore "
    "SST_CONFIG_FILE_PATH=/opt/sim/simulator/config/sst-plugins.conf "
    "SST_CONFIG_FILE_PATH_SEPARATOR=: && ulimit -n 524288 && "
    'exec timeout -k 12s 1360s taskset -c "$1" sst -n 40 ./audited_launcher.py'
)


def utc():
    return datetime.now(timezone.utc).isoformat()


def simulated_processes():
    pids = []
    for status in Path("/proc").glob("[0-9]*/stat"):
        try:
            text = status.read_text()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        name, _, rest = text.partition(")")
        if name.partition("(")[2] in ("sst", "sstsim.x") and rest.split()[0] != "Z":
            pids.append(int(status.parent.name))
    return sorted(pids)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append_receipt(data):
    temp = RECEIPT.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(RECEIPT)


def logged(command, name, timeout):
    started = time.monotonic()
    with (CASE / name).open("xb") as log:
        result = subprocess.run(command, cwd=CASE, stdout=log, stderr=subprocess.STDOUT,
                                timeout=timeout)
    return result.returncode, round(time.monotonic() - started, 3)


def completion_values():
    lines = (CASE / "run.log").read_text(errors="replace").splitlines()
    pattern = re.compile(r"Simulation is complete, simulated time:\s*([.\d]+)\s+(\w+)")
    completion = [match.groups() for line in lines if (match := pattern.search(line))]
    errors = [line.strip()[:240] for line in lines
              if re.search(r"FATAL|SIGSEGV|Traceback|unsupported instruction|^error:", line, re.I)]
    return completion, errors[:5]


def run():
    preflight = json.loads((CASE / "direct-preflight.json").read_text())
    if preflight["status"] != "READY" or sha(CASE / "dispatch.o") != ELF_SHA:
        raise ValueError("candidate ELF differs from the prepared object")
    for name in ("workload.yml", "workload_config.yml", "v310_config/workload_config.yml"):
        if sha(CASE / name) != WORKLOAD_SHA:
            raise ValueError("workload identity mismatch")
    if RECEIPT.exists() or (CASE / "run.log").exists() or (CASE / "bin").exists():
        raise ValueError("candidate case already contains run evidence")
    started_wait = time.monotonic()
    busy_pids = simulated_processes()
    while busy_pids:
        if time.monotonic() - started_wait >= 1100:
            return {"phase": "waiting", "status": "SST_BUSY", "sst_pids": busy_pids}
        time.sleep(5)
        busy_pids = simulated_processes()
    state = {
        "status": "RUNNING", "phase": "sst", "case": str(CASE),
        "started_utc": utc(), "elf_sha256": ELF_SHA, "workload_sha256": WORKLOAD_SHA,
        "workers": 40, "cpu_affinity": AIV_AFFINITY,
    }
    append_receipt(state)
    try:
        code, wall = logged(["bash", "-c", COMMAND, "dispatch-sst", AIV_AFFINITY],
                            "run.log", 1377)
        completion, errors = completion_values()
        state.update(sst_rc=code, sst_wall_seconds=wall, completion_times=completion,
                     key_errors=errors, sst_finished_utc=utc())
        if code or not completion or len(set(completion)) != 1 or errors:
            raise ValueError("SST did not finish with an unambiguous completion record")
        state["phase"] = "readback"
        append_receipt(state)
        code, wall = logged(["python3", "-B", "output_generator.py", "v310_config"],
                            "output-generator.log", 75)
        state.update(output_generator_rc=code, output_generator_wall_seconds=wall)
        if code:
            raise ValueError("output generator did not finish successfully")
        state.update(status="OUTPUT_READY", phase="validation_pending",
                     config_load_audit_sha256=sha(CASE / "config-load.jsonl"),
                     bin_files=sum(1 for path in (CASE / "bin").glob("*.bin")))
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        state.update(status="FAILED", error=str(error)[:240], error_type=type(error).__name__)
    state.update(last_update_utc=utc())
    append_receipt(state)
    return state


def main():
    try:
        result = run()
    except (OSError, ValueError) as error:
        result = {"phase": "preflight", "status": "FAILED", "error": str(error)[:240]}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
