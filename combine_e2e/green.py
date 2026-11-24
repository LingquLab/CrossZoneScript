# Copyright (c) 2026 TileXR Project.
"""Invoke bounded task scripts over the existing green Windows SSH identity."""
import argparse
import json
import os
import subprocess
from pathlib import Path

SESSION = "ea219fe0-2177-4850-84f0-c0da68a4a5fa"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    assert args in ({"action": "preflight"}, {"action": "preflight-diagnose"}), "unknown_action"
    logs = Path(os.environ["LOCALAPPDATA"]) / "CrossZone/tilexr" / SESSION
    logs.mkdir(parents=True, exist_ok=True)
    if args["action"] == "preflight-diagnose":
        stdout = (logs / "preflight.stdout").read_bytes()
        stderr = (logs / "preflight.stderr").read_bytes()
        combined = (stdout + stderr).decode("utf-8", errors="replace").lower()
        markers = ("permission denied", "host key verification failed", "connection timed out",
                   "connection refused", "no route to host", "unexpected_host", "unexpected_architecture",
                   "docker_unavailable", "image_identity_mismatch", "filenotfounderror",
                   "timeoutexpired", "jsondecodeerror", "permissionerror", "command not found",
                   "unable to negotiate", "no such file or directory")
        result = dict(stdout_bytes=len(stdout), stderr_bytes=len(stderr),
                      errors=[name for name in markers if name in combined])
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
                if isinstance(parsed, dict) and parsed.get("ok") is True:
                    result["preflight"] = parsed
            except (ValueError, UnicodeError):
                result["stdout_json"] = False
        print(json.dumps(result, separators=(",", ":")))
        return
    source = Path(__file__).with_name("host_preflight.py").read_bytes()
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    argv = ["ssh", "-T", "-i", str(key), "-p", "22", "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12",
            "root@141.61.94.26", "timeout --kill-after=5 65 python3 -"]
    proc = subprocess.run(argv, input=source, capture_output=True, timeout=80)
    (logs / "preflight.stdout").write_bytes(proc.stdout)
    (logs / "preflight.stderr").write_bytes(proc.stderr)
    assert proc.returncode == 0, "preflight_failed_see_green_logs"
    assert not proc.stderr, "unexpected_stderr_see_green_logs"
    assert len(proc.stdout) < 16000, "summary_too_large"
    result = json.loads(proc.stdout)
    assert result["ok"] is True
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
