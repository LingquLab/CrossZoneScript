"""Build and run the installed SDK's HCCL AIV-only qualification on eight devices."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time

import remote

root = remote.root
base = root / "hccl-aiv-only"
base.mkdir()
sdk = Path(remote.TOOLKIT)
source = sdk / "tools/hccl_test"
shell = ["bash", "-c", 'source "$1/set_env.sh" >/dev/null 2>&1 || exit 66; shift; exec "$@"',
         "tilexr", str(sdk)]
result = {"log_dir": str(root), "sdk": sdk.name, "devices": list(range(8)), "mode": "aiv_only"}


def main():
    if list(root.parent.glob("*/hccl-aiv-only/launch.log")):
        result["status"] = "qualification_already_launched"
        return
    compiler = shutil.which("mpicxx")
    launcher = shutil.which("mpirun")
    if not compiler or not launcher or not (source / "Makefile").is_file():
        result["status"] = "toolkit_source_or_mpi_unavailable"
        return
    lib = sdk / "lib64"
    if not (lib / "libhccl.so").is_file():
        lib = sdk / "aarch64-linux/lib64"
    cpp = [str(path) for path in sorted((source / "common/src").glob("*.cc"))]
    cpp += [str(source / "opbase_test/hccl_allreduce_rootinfo_test.cc")]
    executable = base / "all_reduce_test"
    args = [compiler, "-std=c++17", "-O2", "-I" + str(source / "common/src"),
            "-I" + str(source / "opbase_test"), "-I" + str(sdk / "include"),
            "-I" + str(sdk / "aarch64-linux/include"), *cpp, "-L" + str(lib),
            "-Wl,-rpath-link," + str(lib), "-Wl,--copy-dt-needed-entries", "-lhccl",
            "-lacl_rt", "-lmsprofiler", "-o", str(executable)]
    if not remote.command("hccl-build", shell + args, 125):
        result.update(status="build_failed", errors=remote.result.get("first_errors"))
        return
    result["sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    for _ in range(24):
        occupancy = remote.device_status()
        if occupancy["idle_devices"] == list(range(8)):
            break
        time.sleep(5)
    else:
        result.update(status="devices_busy", occupancy=occupancy)
        return
    cmd = [launcher, "--tag-output", "-n", "8", str(executable), "-b", "8K", "-e", "64K", "-f", "2",
           "-d", "fp32", "-o", "sum", "-p", "8", "-a", "aiv_only", "-c", "2", "-w", "2", "-n", "5"]
    (base / "command.json").write_text(json.dumps(cmd))
    log = base / "launch.log"
    with log.open("wb") as output:
        process = subprocess.run(shell + cmd, stdout=output, stderr=subprocess.STDOUT, timeout=140)
    lines = log.read_text(errors="replace").splitlines()
    success = [line for line in lines if re.search(r"\|\s*success\s*$", line, re.I)]
    failures = [line for line in lines if re.search(
        r"check result failed|this is an error|exec.*failed|failed to|error.*rank|invalid option|not support",
        line, re.I)]
    tags = sorted({int(m.group(1)) for line in lines if (m := re.search(r"\[\d+,(\d+)\]", line))})
    result.update(status="pass" if process.returncode == 0 and len(success) == 4 and not failures
                  and tags == list(range(8)) else "qualification_failed_or_incomplete",
                  exit_code=process.returncode, size_rows=len(success), stdout_ranks=tags,
                  failures=[re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "[ip]", x)[:190] for x in failures[:4]])


try:
    main()
except (OSError, ValueError, subprocess.SubprocessError) as exc:
    result["status"] = type(exc).__name__
(root / "hccl-result.json").write_text(json.dumps(result, indent=2) + "\n")
print("TILEXR_RESULT " + json.dumps(result, ensure_ascii=True, separators=(",", ":")))
