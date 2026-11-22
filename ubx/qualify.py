"""Run the official AIV HCCL Test once for an exact two-node EP32 environment."""

from concurrent.futures import ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from campaign import BASE, occupancy, process


CANN = Path("/usr/local/Ascend/cann-9.2.T101")
MPI = Path("/usr/local/mpich-3.2.1")
TEST = CANN / "tools/hccl_test/bin/all_reduce_test"
DRIVER = Path("/usr/local/Ascend/driver/version.info")
SETUP = f'source {CANN}/set_env.sh && export LD_LIBRARY_PATH={MPI}/lib:{MPI}/lib64:$LD_LIBRARY_PATH && exec "$@"'


def child(args, timeout=30, check=True):
    return subprocess.run(args, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=timeout)


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identify(address):
    acl = ctypes.CDLL("libascendcl.so")
    count = ctypes.c_uint32()
    if acl.aclInit(None) != 0:
        raise RuntimeError("ACL initialization failed")
    try:
        if acl.aclrtGetDeviceCount(ctypes.byref(count)) != 0 or count.value != 16:
            raise RuntimeError(f"expected 16 logical devices, got {count.value}")
    finally:
        acl.aclFinalize()
    addrs = json.loads(child(["ip", "-j", "address", "show"]).stdout)
    interfaces = [item["ifname"] for item in addrs
                  if any(ip.get("local") == address for ip in item.get("addr_info", []))]
    if len(interfaces) != 1:
        raise RuntimeError("could not identify the communication interface")
    driver = {k.lower(): v for k, v in (line.split("=", 1) for line in DRIVER.read_text().splitlines() if "=" in line)
              if k.lower() in ("version", "package_version", "firmware_version")}
    firmware = child(["npu-smi", "info", "-t", "board", "-i", "0", "-c", "0"], check=False)
    firmware_lines = [line.strip() for line in firmware.stdout.splitlines()
                      if re.search(r'firmware|version|board id|device name', line, re.I)]
    visible = [int(i) for i in re.findall(r"NPU ID\s*:\s*(\d+)", child(["npu-smi", "info", "-l"]).stdout)]
    if sorted(visible) != list(range(16)):
        raise RuntimeError("unexpected physical-device list")
    return {
        "host": address, "devices": list(range(16)), "interface": interfaces[0],
        "cann": str(CANN.resolve()), "driver": driver, "board_versions": firmware_lines,
        "test_sha256": hash_file(TEST), "libhccl_sha256": hash_file(CANN / "lib64/libhccl.so"),
    }


def state(values):
    values = dict(time=datetime.now(timezone.utc).isoformat(), **values)
    temporary = BASE / "qualification-status.part"
    temporary.write_text(json.dumps(values) + "\n", encoding="utf-8")
    temporary.replace(BASE / "qualification-status.json")


def capture_launch(argv, output, environment, timeout):
    with (output / "stdout.log").open("w") as out, (output / "stderr.log").open("w") as err:
        return subprocess.run(argv, timeout=timeout, env=environment, stdout=out, stderr=err).returncode


def run(primary, peer):
    other = peer.rsplit("@", 1)[-1]
    nodes = []
    for host, address in ((None, primary), (peer, other)):
        command = process(host, ["/bin/bash", "-c", SETUP, "hccl", sys.executable, str(Path(__file__)), "identify", address])
        nodes.append(json.loads(child(command).stdout))
    fingerprint = {"nodes": nodes, "ranks": 32, "cards_per_node": 16, "accelerator": "aiv_only"}
    key = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:20]
    output = BASE.parent / "qualifications" / ("ep32-aiv-" + key)
    output.mkdir(parents=True, exist_ok=True)
    with (output / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (output / "launch.json").exists():
            result = output / "result.json"
            if result.is_file():
                state(json.loads(result.read_text()))
            else:
                state({"phase": "prior_attempt_unresolved", "path": str(output)})
            return
        mpi = [str(MPI / "bin/mpirun"), "-launcher", "ssh", "-hosts", f"{primary}:16,{other}:16",
               "-n", "32", "-ppn", "16", "-l"]
        test = [str(TEST), "-a", "aiv_only", "-p", "16", "-b", "8K", "-e", "8K",
                "-n", "1", "-w", "0", "-d", "fp32", "-o", "sum", "-c", "2"]
        argv = ["/bin/bash", "-c", SETUP, "hccl", *mpi, *test]
        interfaces = ",".join(dict.fromkeys(node["interface"] for node in nodes))
        environment = dict(os.environ, HCCL_SOCKET_IFNAME=interfaces, HCCL_SOCKET_FAMILY="AF_INET",
                           HCCL_CONNECT_TIMEOUT="180", HCCL_EXEC_TIMEOUT="180",
                           HCCL_HOST_SOCKET_PORT_RANGE="auto", ASCEND_GLOBAL_LOG_LEVEL="3",
                           HCCL_TEST_USE_DEVS=",".join(map(str, range(16))),
                           HYDRA_LAUNCHER_EXTRA_ARGS="-o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no")
        sanity = subprocess.run([*mpi, "/bin/hostname"], capture_output=True,
                                text=True, timeout=60, check=True, env=environment)
        if len(re.findall(r"^\[\d+\]", sanity.stdout, re.MULTILINE)) != 32:
            raise RuntimeError("MPI did not start 32 independently labelled ranks")
        last = None
        while True:
            with ThreadPoolExecutor(max_workers=2) as pool:
                result = pool.submit(child, process(peer, [sys.executable, str(BASE / "campaign.py"), "probe",
                                                           str(BASE / "ready/stages-off")]), 16)
                local = occupancy()
                try:
                    remote = result.result().stdout.strip().split()[-1]
                except (OSError, subprocess.SubprocessError, IndexError):
                    remote = "?" * 16
            if local == remote == "I" * 16:
                break
            if (local, remote) != last:
                state({"phase": "waiting_32p", "local": local, "peer": remote, "path": str(output)})
                last = (local, remote)
            time.sleep(5)
        attempt = {"environment": fingerprint, "argv": argv, "interface": interfaces,
                   "time": datetime.now(timezone.utc).isoformat()}
        with (output / "launch.json").open("x") as handle:
            json.dump(attempt, handle, indent=2)
        state({"phase": "running", "path": str(output), "ranks": 32})
        try:
            rc = capture_launch(argv, output, environment, 540)
        except subprocess.TimeoutExpired:
            rc = 124
        combined = "\n".join((output / log).read_text(errors="replace")[-200000:]
                             for log in ("stdout.log", "stderr.log"))
        labels = sorted({int(i) for i in re.findall(r"^\[(\d+)\]", combined, re.MULTILINE)})
        failed = re.search(r"hccl interface return err|hccl_op_base execute failed|\|\s*fail(?:ed)?\s*$|ERROR|invalid, npus",
                           combined, re.M | re.I)
        successes = len(re.findall(r"\|\s*success\s*$", combined, re.M | re.I))
        phase = "passed" if rc == 0 and not failed and successes and len(labels) == 32 else (
            "failed" if rc or failed else "unverified")
        result = {"phase": phase, "path": str(output), "ranks": 32, "exit_code": rc,
                  "labelled_ranks": labels, "success_rows": successes}
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        state(result)


if __name__ == "__main__":
    if sys.argv[1] == "identify":
        print(json.dumps(identify(sys.argv[2])))
    else:
        try:
            run(*sys.argv[2:])
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            state({"phase": "preflight_failed", "reason": str(exc)})
            raise
