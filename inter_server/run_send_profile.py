"""Run the baseline T4 with SDK stage markers and save the trace in Green."""

import base64
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
resources = root / "resources"
profile = resources / "send_profile"
bulk = resources / "bulk_forward"


def encoded(path):
    return base64.b64encode(path.read_bytes()).decode("ascii")


request = {
    "build": json.loads((profile / "build-record.json").read_text(encoding="utf-8")),
    "layout": json.loads((bulk / "layout.json").read_text(encoding="utf-8")),
    "object": encoded(profile / "inter_server_send.o.gz"),
    "forward_images": encoded(bulk / "forward-images.tar.gz"),
    "profile": {"sdk": encoded(profile / "sdk.zip"), "adapter": encoded(profile / "profile_trace.py")},
}
runner = (root / "run_mte_t4_remote.py").read_text(encoding="utf-8").rsplit("if __name__ == ", 1)[0]
stage = (root / "run_bulk_forward_remote.py").read_text(encoding="utf-8")
capture = (root / "run_send_profile_remote.py").read_text(encoding="utf-8")
source = runner + "\n" + stage + "\n" + capture + "\nprint(json.dumps(run_bulk(" + repr(request) + "), ensure_ascii=True))\n"
compile(source, "<profiled_runner>", "exec")
host = "root@141.61.94.58"
options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
proc = subprocess.run(["ssh", "-T", *options, host, "python3", "-"], input=source.encode("utf-8"),
                      capture_output=True, timeout=2250)
proc.check_returncode()
result = json.loads(proc.stdout)["t4"]
assert result["run"]["status"] == "sst_and_checker_passed" and result["profile"]["streams"] == 1024
dest = Path("D:/project/tilexr-inter-server-sst/t4-profile-94e104f0")
dest.mkdir(parents=True, exist_ok=True)
artifact = "perfetto_trace.json"
remote = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-profile-94e104f0/profile-report"
subprocess.run(["scp", "-q", *options, f"{host}:{remote}/{artifact}", str(dest / artifact)],
               capture_output=True, timeout=120, check=True)
import hashlib
assert hashlib.sha256((dest / artifact).read_bytes()).hexdigest() == result["profile"]["trace_sha256"]
result["green_perfetto_file"] = str(dest / artifact)
print(json.dumps(result, ensure_ascii=True))
