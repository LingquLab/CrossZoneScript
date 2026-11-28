"""Execute a pinned EP8 smoke test on the configured public_fp8 SSH entry."""
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

here = Path(__file__).resolve().parent
folder = Path("D:/project/UBX攻关/环境信息/yundao")
raw = (folder / "实机环境登陆.txt").read_bytes()
for encoding in ("utf-8-sig", "gb18030", "utf-16"):
    try:
        lines = raw.decode(encoding).splitlines()
        break
    except UnicodeError:
        continue
if not lines:
    raise SystemExit("Cannot read public_fp8 login notes")
command, = [line for line in lines if re.search(r"\bssh\b", line)
            and re.search(r"(?:^|\s)-p\s+30527(?:\s|$)", line)]
target = re.search(r"[\w.-]+@[\w.-]+", command).group()
identity = re.search(r"""(?:^|\s)-i\s+(?:"([^"]+)"|'([^']+)'|([^\s]+))""", command)
value = next(s for s in identity.groups() if s)
path = Path(value.replace("\\", "/")).expanduser()
choices = [path, folder / path, folder.parent / path, folder.parent.parent / path]
if value[:3].lower() == "/d/":
    choices.append(Path("D:/" + value[3:]))
key = next(p for p in choices if p.is_file())
ssh = shutil.which("ssh")
with tempfile.TemporaryDirectory(prefix="tilexr-public-fp8-") as temp:
    known_hosts = (Path(temp) / "known_hosts").as_posix()
    args = [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=" + known_hosts, "-o", "GlobalKnownHostsFile=none",
            "-o", "UpdateHostKeys=no", "-o", "IdentitiesOnly=yes", "-i", str(key),
            "-p", "30527", target, "python3", "-"]
    options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
    entry = "inspect.py" if options.get("inspect") else "remote.py"
    if options.get("rootinfo"):
        entry = "rootinfo.py"
    if options.get("profile_read"):
        entry = "profile_read.py"
    if options.get("matrix_status"):
        entry = "matrix_status.py"
    if options.get("matrix"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in ("matrix.py", "remote.py", "qp_probe.cpp", "final_bench.cpp",
                         "ep8_modules.cpp", "native_send_probe.cpp", "ep8_concurrent.cpp"):
                archive.add(here / name, arcname=name)
        remote = ('umask 077; d=$(mktemp -d /tmp/tilexr-ep8-matrix.XXXXXXXX) && '
                  'tar -xzf - -C "$d" && python3 "$d/matrix.py"')
        if options.get("device"):
            remote += " device"
        elif options.get("modules"):
            remote += " modules"
        elif options.get("profile"):
            remote += " profile"
        elif options.get("finish"):
            remote += " finish"
        elif options.get("remaining"):
            remote += " remaining"
        elif options.get("concurrent"):
            remote += " concurrent"
        proc = subprocess.run(args[:-2] + [remote], input=buffer.getvalue(), capture_output=True, timeout=1210)
    elif options.get("stage_source"):
        from source_bundle import prepare
        packed, status = prepare(here / "remote.py", Path(temp))
        if packed is None:
            print(json.dumps(status, ensure_ascii=True, separators=(",", ":")))
            raise SystemExit(0)
        remote = ('umask 077; d=$(mktemp -d /tmp/tilexr-ep8-source.XXXXXXXX) && '
                  'tar -xzf - -C "$d" && python3 "$d/remote.py" "$d"')
        proc = subprocess.run(args[:-2] + [remote], input=packed, capture_output=True, timeout=820)
    else:
        source = (here / entry).read_bytes()
        if options.get("resume_run"):
            source = b'RESUME_RUN = True\n' + source
        if options.get("resume_build"):
            source = b'RESUME_BUILD = True\n' + source
        proc = subprocess.run(args, input=source, capture_output=True, timeout=820)
output = proc.stdout.decode(errors="replace").splitlines()
lines = [s.removeprefix("TILEXR_RESULT ") for s in output if s.startswith("TILEXR_RESULT ")]
if lines:
    print(json.dumps(json.loads(lines[-1]), ensure_ascii=True, separators=(",", ":")))
else:
    stderr = proc.stderr.decode(errors="replace").lower()
    reason = next((message for message in ("connection timed out", "connection reset", "connection closed",
        "broken pipe", "permission denied", "host key verification failed", "could not resolve",
        "no route to host", "connection refused") if message in stderr), None)
    print(json.dumps(dict(stage="ssh_or_remote_start", ssh_exit=proc.returncode, reason=reason,
                          stderr_bytes=len(proc.stderr), stdout_bytes=len(proc.stdout))))
