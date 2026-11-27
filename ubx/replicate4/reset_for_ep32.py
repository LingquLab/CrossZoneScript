"""Reset the EP32 device set with the recorded run47 command, or read back its progress."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex

from benchmark import HOSTS, ssh
from retest_local_receive import WORK

REMOTE = r"""
import json
import re
import sys

mode = sys.argv[1]
work = Path(sys.argv[2])
ROOT = work
OUTPUT = work / "reset-ep32-20261127"
devices = [int(device) for device in sys.argv[3:]]
output = work / "reset-ep32-20261127.jsonl"
assert devices and all(0 <= device < 16 for device in devices)
assert len(devices) == len(set(devices))

def rows():
    return [json.loads(line) for line in output.read_text().splitlines()] if output.is_file() else []

def save(row):
    with output.open("a") as handle:
        handle.write(json.dumps(row) + "\n")

def query_health(device):
    try:
        result = subprocess.run(["npu-smi", "info", "-t", "health", "-i", str(device)],
                                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return dict(status="unknown", codes=[], exit_code=None)
    status = re.search(r"Health Status\s*:\s*(\w+)", result.stdout, re.I)
    codes = re.search(r"Error Code\s*:\s*([^\r\n]+)", result.stdout, re.I)
    detail = re.search(r"Error Information\s*:\s*([^\r\n]+)", result.stdout, re.I)
    return dict(status=status[1] if status else "unknown", codes=codes[1].split() if codes else [],
                error_info=detail[1].strip()[:180] if detail else None,
                exit_code=result.returncode)

def occupancy(device):
    try:
        return len(device_occupants(device))
    except (subprocess.SubprocessError, AssertionError):
        return None

def status():
    history = rows()
    with ThreadPoolExecutor(max_workers=8) as pool:
        devices_state = list(pool.map(lambda device: dict(device=device, occupancy=occupancy(device),
                                                          **query_health(device)), devices))
    done = {row["device"] for row in history if row.get("reset_exit") == 0}
    return dict(devices=devices_state, reset_completed=sorted(done), history=history)

if mode == "reset":
    # A completed reset and the latest device health are reported separately.
    history = rows()
    completed = {row["device"] for row in history if row.get("reset_exit") == 0}
    current_occupants = occupants()
    if current_occupants and "root_launchers" in globals():
        for attempt in range(4):
            if not current_occupants:
                break
            for pid, (identity, name) in root_launchers(current_occupants).items():
                if process(pid) == identity:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        continue
                    save(dict(device=devices[0], stage="stop_mpi_launcher", program=name))
            clear_occupants(current_occupants)
            current_occupants = occupants()
    elif current_occupants:
        clear_occupants(current_occupants)
    for device in devices:
        if device in completed:
            continue
        before = query_health(device)
        occupied = occupancy(device)
        if occupied != 0:
            save(dict(device=device, before=before, occupancy=occupied, stopped="device busy or unknown"))
            continue
        row = dict(device=device, before=before, occupancy=0)
        command = ["npu-smi", "set", "-t", "reset", "-i", str(device), "-m", "1"]
        save(dict(device=device, stage="reset_command_started"))
        try:
            result = subprocess.run(command, input="y\n", capture_output=True, text=True, timeout=110)
        except subprocess.TimeoutExpired:
            save(dict(**row, stopped="reset command timed out"))
            break
        row["reset_exit"] = result.returncode
        row["reset_output"] = (result.stdout + result.stderr).strip().splitlines()[-4:]
        if result.returncode != 0:
            save(row)
            break
        time.sleep(20)
        row["after"] = query_health(device)
        save(row)
    print(json.dumps(status(), separators=(",", ":")))
else:
    assert mode == "status"
    print(json.dumps(status(), separators=(",", ":")))
"""

def inspect(host, mode, devices, mpi_cleanup):
    helpers = Path(__file__).with_name("run_remote.py").read_text().split("def preflight(", 1)[0]
    if mpi_cleanup and mode == "reset":
        from release_mpi_occupants import PROBE
        helpers += PROBE[PROBE.index("def process("):PROBE.index("signal_counts =")]
    result = ssh(host, shlex.join(["python3", "-", mode, WORK, *map(str, devices)]),
                 payload=(helpers + REMOTE).encode(), timeout=780 if mode == "reset" else 70,
                 stage="NPU " + mode)
    return dict(host=host, **json.loads(result))

def compact(report):
    health_codes = {}
    for row in report["devices"]:
        for code in row["codes"]:
            health_codes.setdefault(code, {"devices": [], "example": row.get("error_info")})["devices"].append(row["device"])
    return dict(host=report["host"], healthy=sum(row["status"].lower() == "ok" for row in report["devices"]),
                idle=sum(row["occupancy"] == 0 for row in report["devices"]),
                busy=[row["device"] for row in report["devices"] if row["occupancy"]],
                unknown_occupancy=[row["device"] for row in report["devices"] if row["occupancy"] is None],
                reset_completed=report["reset_completed"],
                health_codes=health_codes,
                unknown_health=[row["device"] for row in report["devices"] if row["status"] == "unknown"],
                blocked=[row for row in report["history"] if row.get("stopped") or row.get("reset_exit", 0) != 0][-5:])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    mode = args["mode"]
    assert mode in {"reset", "status"}
    target_hosts = args.get("hosts", list(HOSTS))
    assert target_hosts and set(target_hosts) <= set(HOSTS)
    devices = args.get("devices", list(range(16)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(lambda host: inspect(host, mode, devices, args.get("mpi_cleanup", False)), target_hosts))
    output = Path(args["destination"]) / "replicate4-local-receive-bs32-093dbcd-diagnostics" / "npu-reset-evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if set(target_hosts) != set(HOSTS):
        output = output.with_name("npu-reset-" + target_hosts[0].rsplit(".", 1)[-1] + "-evidence.json")
    output.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(local_diagnostics=str(output), hosts=list(map(compact, reports))), separators=(",", ":")))
