"""Rebuild Perfetto from the completed run and transfer the two report files."""
import hashlib
import json
from pathlib import Path
import subprocess

from green_auth import askpass_environment

root = Path(__file__).resolve().parent
code = "\n".join(((root / "fused_profile.py").read_text().split('\nif __name__ == "__main__":')[0],
                  (root / "fused_profile_recover.py").read_text().split('\nif __name__ == "__main__":')[0]))
code += '\nimport contextlib, io\nwith contextlib.redirect_stdout(io.StringIO()):\n    result = recover()\nprint(json.dumps(result,separators=(",",":")))\n'
compile(code, "<recover_bs4_profile>", "exec")
options = ["-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
           "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no"]
host = "root@141.61.94.58"
with askpass_environment() as environment:
    process = subprocess.run(["ssh", "-T", *options, host, "python3", "-"], input=code.encode(),
                             env=environment, capture_output=True, timeout=180)
    if process.returncode:
        import re
        error = process.stderr.decode(errors="replace").splitlines()
        cls = re.match(r"(\w+(?:Error|Exception)):", error[-1]) if error else None
        result = {"status": "remote_read_failed", "ssh_exit": process.returncode, "error_type": cls.group(1) if cls else None}
    else:
        result = json.loads(process.stdout)
    if result.get("status") == "rendered":
        case = "t4-combine-fp32-ea1ca889-profile"
        assert result["case"] == case
        remote = "/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/" + case
        dest = Path("D:/project/tilexr-inter-server-sst") / case
        dest.mkdir(parents=True, exist_ok=True)
        for name, digest in (("perfetto_trace.json", "trace_sha256"), ("profile-summary.json", "summary_sha256")):
            path = dest / name
            temporary = path.with_suffix(".part")
            copy = subprocess.run(["scp", "-q", *options, f"{host}:{remote}/{name}", str(temporary)],
                                  env=environment, capture_output=True, timeout=120)
            if copy.returncode or hashlib.sha256(temporary.read_bytes()).hexdigest() != result[digest]:
                result["copy_error"] = "scp_or_hash_mismatch"
                break
            temporary.replace(path)
            result[name] = str(path)
print(json.dumps(result, separators=(",", ":")))
