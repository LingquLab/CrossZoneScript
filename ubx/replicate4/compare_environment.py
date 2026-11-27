"""Compare the last passing BS32 run with the current hosts and failed retest."""
from concurrent.futures import ThreadPoolExecutor
import json
import shlex

from benchmark import HOSTS, ssh
from retest_local_receive import CONTROL_WORK, WORK

PROBE = r"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

old, current = map(Path, sys.argv[1:])
receipt_file = old / "full-bs32-f50/host-result.json"
def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else {}

def command(*args):
    try:
        result = subprocess.run(args, capture_output=True, timeout=8, text=True, errors="replace")
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""

def utc(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()

def versions(path):
    if not path.is_file():
        return {}
    return {k.lower(): v for k, v in re.findall(
        r"(?mi)^\s*(version|package_version|firmware_version|driver_version)\s*[=:]\s*([\w.()\-]+)",
        path.read_text(errors="replace"),
    )}

def health(device):
    result = command("npu-smi", "info", "-t", "health", "-i", str(device))
    status = re.search(r"Health Status\s*:\s*(\w+)", result)
    codes = re.search(r"Error Code\s*:\s*([^\r\n]+)", result)
    return (status[1] if status else "Unknown",
            re.findall(r"\b[0-9A-F]{8}\b", codes[1], re.I) if codes else [])

def board_info(device):
    result = command("npu-smi", "info", "-t", "board", "-i", str(device))
    return dict(re.findall(r"(?mi)^\s*(Firmware Version|NPU Firmware Version|Chip Name|Board Version)\s*:\s*([\w.()\-]+)", result))

toolkit = Path("/usr/local/Ascend/ascend-toolkit/set_env.sh").resolve()
version_paths = [toolkit.parent / "version.info", toolkit.parent / "version.cfg",
                 toolkit.parent / "latest/version.info", Path("/usr/local/Ascend/cann-9.2.T101/version.info")]
toolkit_versions = {str(path): data for path in version_paths if (data := versions(path))}
driver = versions(Path("/usr/local/Ascend/driver/version.info"))
firmware = versions(Path("/usr/local/Ascend/firmware/version.info"))
with ThreadPoolExecutor(max_workers=8) as pool:
    checks = list(pool.map(health, range(16)))
summary_old, summary_new = [read_json(root / "build-result.json") for root in (old, current)]
fields = ("runtime_revision", "runtime_sha256", "clock_revision")
same_build_inputs = {key: summary_old.get(key) == summary_new.get(key) and key in summary_old for key in fields}
same_clock = all(summary_old.get("artifacts", {}).get(k) and
                 summary_old["artifacts"][k] == summary_new.get("artifacts", {}).get(k)
                 for k in ("ubx_clock_calibrate", "ubx_clock_wait.o"))
caches = [(root / "dispatch-build/CMakeCache.txt").read_text(errors="replace")
          for root in (old, current) if (root / "dispatch-build/CMakeCache.txt").is_file()]
def compiler(cache):
    return dict(re.findall(r"(?m)^(CMAKE_CXX_COMPILER|UBX_ASCENDC_COMPILER|ASCENDC_COMPILER):[^=]+=([^\r\n]+)", cache))
prev = read_json(receipt_file)
now = time.time()
last_success = receipt_file.stat().st_mtime if receipt_file.is_file() else None
boot = re.search(r"(?m)^btime (\d+)$", Path("/proc/stat").read_text())
boot_time = int(boot[1]) if boot else None
package_time = {}
for name, binary in (("toolkit_env", toolkit), ("driver_version", Path("/usr/local/Ascend/driver/version.info")),
                     ("firmware_version", Path("/usr/local/Ascend/firmware/version.info"))):
    if binary.is_file():
        metadata = binary.stat()
        package_time[name] = dict(mtime_utc=utc(metadata.st_mtime),
                                  ctime_utc=utc(metadata.st_ctime),
                                  stat_after_success=bool(last_success and max(metadata.st_ctime, metadata.st_mtime) > last_success))
toolkit_caches = [compiler(cache) for cache in caches]
print(json.dumps(dict(checked_utc=utc(now), kernel=command("uname","-r").strip(),
    boot_utc=utc(boot_time) if boot_time else None,
    reboot_since_success=(boot_time > last_success if boot_time and last_success else None),
    prior_success_utc=utc(last_success) if last_success else None,
    prior_passed=prev.get("passed"), prior_ranks=len(prev.get("ranks", [])),
    prior_route_sha256=prev.get("route_sha256"),
    driver=driver, firmware=firmware, board0=board_info(0), toolkit=toolkit_versions,
    set_env=str(toolkit), package_metadata=package_time, compilers=toolkit_caches,
    same_build_inputs=same_build_inputs, same_clock_artifacts=bool(same_clock),
    health=dict(Counter(status for status, _ in checks)),
    health_codes=dict(Counter(code for _, codes in checks for code in codes)),
    devices_service_timeout=[i for i, (_, codes) in enumerate(checks) if "80CB800A" in codes]),
    separators=(",", ":")))
"""

def inspect(host):
    args = shlex.join(["python3", "-", CONTROL_WORK, WORK])
    return dict(host=host, **json.loads(ssh(host, args, payload=PROBE.encode(), timeout=70)))


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        hosts = list(pool.map(inspect, HOSTS))
    print(json.dumps(dict(read_only=True, hosts=hosts), separators=(",", ":")))
