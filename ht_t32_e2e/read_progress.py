"""Read SST progress over the existing green-local authentication."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile

from green_auth import askpass_environment

root = Path(__file__).resolve().parent
support = root.parent / "inter_server"
coverage_dir = root.parent / "ht_t32_combined"
sys.path.insert(0, str(coverage_dir))
from coverage import coverage

row_groups = {}
with tarfile.open(coverage_dir / "send/fixture-images.tar.gz") as tar:
    _, payload_rings = coverage(tar, payload_groups=row_groups)
    with tarfile.open(root / "fixture-images.tar.gz") as fused:
        for host in payload_rings:
            for name in ("counts.bin", "ranges.bin", "forward.bin"):
                path = f"input_bin/rank{host:02d}/{name}"
                assert tar.extractfile(path).read() == fused.extractfile(path).read(), path
rings = {host: {ring: 2 * (count - 1) + 1 for ring, count in group.items()}
         for host, group in payload_rings.items()}
groups = {host: {ring: [(2 * start, 2 * count) for start, count in items]
                 for ring, items in ranks.items()} for host, ranks in row_groups.items()}
publish_groups = [item for rank in groups.values() for items in rank.values() for item in items]
assert sum(count for _, count in publish_groups) == 7020
assert sum(start // 8 != (start + count - 1) // 8 for start, count in publish_groups) == 338
assert sum(sum(group.values()) for group in rings.values()) == 2 * 3510 + 64 * 56 * 2
trace = (support / "owner_trace_remote.py").read_text().split("def doorbell_coordinates(", 1)[0]
observe = "def read_observed(" + (support / "diagnose_t96_sq_remote.py").read_text().split(
    "def read_observed(", 1)[1].split("def diagnose_t96(", 1)[0]
code = "\n".join(("import time, subprocess", trace, observe, f"RINGS = {rings!r}", f"GROUPS = {groups!r}",
                  (root / "observe.py").read_text(), 'print(json.dumps(check(), separators=(",", ":")))'))
compile(code, "<inspect_e2e_sst>", "exec")
ssh = ["ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8",
       "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o", "PreferredAuthentications=password",
       "-o", "PubkeyAuthentication=no", "root@141.61.94.58", "python3", "-"]
try:
    with askpass_environment() as env:
        reply = subprocess.run(ssh, input=code.encode(), env=env, capture_output=True, timeout=230)
    if reply.returncode == 0:
        result = json.loads(reply.stdout)
    else:
        text = reply.stderr.decode(errors="replace").lower()
        reason = next((kind for phrase, kind in (
            ("permission denied", "authentication_denied"), ("connection timed out", "connect_timeout"),
            ("connection refused", "connect_refused"), ("ssh_askpass", "askpass_failed"),
        ) if phrase in text), "remote_command_failed")
        result = {"ssh_exit": reply.returncode, "reason": reason, "stderr_bytes": len(reply.stderr)}
except FileNotFoundError:
    result = {"reason": "configured_local_auth_note_not_found"}
except subprocess.TimeoutExpired:
    result = {"reason": "command_timeout"}
except ValueError:
    result = {"reason": "invalid_receipt"}
print(json.dumps(result, separators=(",", ":")))
