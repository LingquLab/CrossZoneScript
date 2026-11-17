"""Deploy retained TileXR build artifacts through the green Windows tssh entry point."""

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", required=True)
    options = parser.parse_args()
    args = json.loads(Path(options.args_file).read_text(encoding="utf-8"))
    host = str(ipaddress.IPv4Address(args["host"]))
    revision = args["revision"]
    assert re.fullmatch(r"[0-9a-f]{40}", revision), "invalid_revision"
    uuid.UUID(args["session"])
    pr = int(args["pr"])
    assert pr > 0 and os.name == "nt", "invalid_platform_or_pr"
    operation = args.get("operation", "deploy")
    assert operation in ("deploy", "relocate"), "invalid_operation"
    root = Path(args.get("green_code_root", r"D:\works\code\ai\crosszone"))
    tssh = root / "TileXR/tools/tssh/tssh.cmd"
    assert tssh.is_file(), "approved_tssh_unavailable"
    evidence = Path.cwd() / ("%s-pr%d-%s-%s" % (operation, pr, host, revision[:8]))
    evidence.mkdir(exist_ok=True)

    # Read only the key's location; the key bytes never enter returned evidence.
    candidates = [Path.home() / ".ssh/npass_id_rsa", Path.home() / "npass_id_rsa"]
    for directory in [Path.cwd(), *list(Path.cwd().parents)[:8]]:
        candidates.extend([directory / "npass_id_rsa", directory / ".ssh/npass_id_rsa"])
    key = next((p.resolve() for p in candidates if p.is_file()), None)
    ssh = shutil.which("ssh")
    assert key and ssh, "ssh_or_authorized_key_unavailable"
    assert not any(c in str(key) + ssh for c in "%\r\n"), "unsupported_ssh_path"
    wrapper = evidence / "ssh.cmd"
    wrapper.write_text(
        '@echo off\n"%s" -i "%s" -o BatchMode=yes -o IdentitiesOnly=yes '
        '-o IdentityAgent=none -o StrictHostKeyChecking=yes '
        '-o ServerAliveInterval=5 -o ServerAliveCountMax=3 %%*\n'
        'exit /b %%errorlevel%%\n' % (ssh, key),
        encoding="utf-8",
    )
    inventory = evidence / "hosts.json"
    inventory.write_text(json.dumps({"version": 1, "clusters": {
        "deploy": {"user": "root", "hosts": [{"name": "deploy", "address": host}]}
    }}), encoding="utf-8")
    payload = base64.urlsafe_b64encode(json.dumps(args).encode()).decode("ascii")
    remote = Path(__file__).with_name("deploy_verified_build_remote.py").read_text(encoding="utf-8")
    shell = (
        "set -eo pipefail\n"
        "source /usr/local/Ascend/cann-9.1.0/set_env.sh >/dev/null\n"
        'export LD_LIBRARY_PATH="/usr/local/Ascend/driver/lib64/driver:'
        '/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64:'
        '/usr/local/Ascend/cann-9.1.0/aarch64-linux/lib64:${LD_LIBRARY_PATH:-}"\n'
        'python3 - "$1" <<\'CROSSZONE_REMOTE_PY\'\n' + remote + "\nCROSSZONE_REMOTE_PY\n"
    )
    (evidence / "request.json").write_text(json.dumps(args), encoding="utf-8")
    command = [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"),
               "/d", "/c", str(tssh), "-c", str(inventory), "-C", "deploy",
               "--ssh", str(wrapper), "--connect-timeout", "10", "--timeout", "210",
               "-I", "--", "timeout", "--signal=TERM", "--kill-after=10s", "180s",
               "bash", "-s", "--", payload]
    result = subprocess.run(command, input=shell.encode("utf-8"), capture_output=True, timeout=230)
    (evidence / "stdout.log").write_bytes(result.stdout)
    (evidence / "stderr.log").write_bytes(result.stderr)
    records = [json.loads(line.removeprefix("CROSSZONE_RESULT "))
               for line in result.stdout.decode("utf-8", errors="replace").splitlines()
               if line.startswith("CROSSZONE_RESULT ")]
    summary = {"host": host, "revision": revision, "tssh_exit_code": result.returncode,
               "results": records, "evidence_directory": str(evidence), "npu_tests_run": False}
    (evidence / "result.json").write_text(json.dumps(summary), encoding="utf-8")
    assert result.returncode == 0 and len(records) == 1, "inspect_saved_deployment_evidence"
    record = records[0]
    assert record["status"] == "PASS" and record["revision"] == revision
    assert record["npu_tests_run"] is False and record["current_verified"] is True
    if operation == "relocate":
        assert record["original_deployment_removed"] is True
        assert record["verification_user"] == args["owner"]
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
