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
options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
ssh = shutil.which("ssh")


def profiles():
    for index, line in enumerate(lines):
        port = re.search(r"(?:^|\s)-p\s+(\d+)(?:\s|$)", line)
        if not port or not re.search(r"\bssh\b", line):
            continue
        if port[1] == "30527" or (index and "public_fp8" in lines[index - 1]):
            yield port[1], line


def connection(profile, temp):
    port, command = profile
    target = re.search(r"[\w.-]+@[\w.-]+", command).group()
    identity = re.search(r"""(?:^|\s)-i\s+(?:"([^"]+)"|'([^']+)'|([^\s]+))""", command)
    value = next(s for s in identity.groups() if s)
    path = Path(value.replace("\\", "/")).expanduser()
    choices = [path, folder / path, folder.parent / path, folder.parent.parent / path]
    if value[:3].lower() == "/d/":
        choices.append(Path("D:/" + value[3:]))
    key = next(p for p in choices if p.is_file())
    known_hosts = (Path(temp) / ("known_hosts-" + port)).as_posix()
    return [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "UserKnownHostsFile=" + known_hosts, "-o", "GlobalKnownHostsFile=none",
            "-o", "UpdateHostKeys=no", "-o", "IdentitiesOnly=yes", "-i", str(key),
            "-p", port, target, "python3", "-"]


def available_alternative(temp):
    outcomes = []
    for profile in profiles():
        if profile[0] == "30527":
            continue
        try:
            args = connection(profile, temp)
            proc = subprocess.run(args[:-2] + [
                "test -f /usr/local/Ascend/cann-9.2.2/set_env.sh && npu-smi info"
            ], capture_output=True, timeout=25)
        except StopIteration:
            outcomes.append("identity_missing")
            continue
        except subprocess.TimeoutExpired:
            outcomes.append("probe_timeout")
            continue
        if proc.returncode:
            outcomes.append("ssh_or_sdk_unavailable")
            continue
        status = proc.stdout.decode(errors="replace")
        idle = sorted({int(value) for value in re.findall(r"No running processes found in NPU\s+(\d+)", status)})
        if status.count("Ascend950DT") >= 8 and idle == list(range(8)):
            return args
        outcomes.append("eight_950dt_devices_unavailable")
    print(json.dumps(dict(stage="alternate_host", outcomes=outcomes), ensure_ascii=True))
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix="tilexr-public-fp8-") as temp:
    if options.get("alternate_diagnostics"):
        for profile in profiles():
            if profile[0] == "30527":
                continue
            candidate = connection(profile, temp)
            match = subprocess.run(candidate[:-2] + [
                "test -f ~/tilexr-validation/ht-4d37ae67-multi-jetty-ep8-1795859849/result.json"
            ], capture_output=True, timeout=20)
            if match.returncode == 0:
                args = candidate
                break
        else:
            print(json.dumps(dict(stage="alternate_diagnostics_host", status="unavailable")))
            raise SystemExit(0)
    elif options.get("alternate_host"):
        args = available_alternative(temp)
    else:
        args = connection(next(profile for profile in profiles() if profile[0] == "30527"), temp)
    entry = "inspect.py" if options.get("inspect") else "remote.py"
    if options.get("rootinfo"):
        entry = "rootinfo.py"
    if options.get("hccl_inventory"):
        entry = "hccl_inventory.py"
    if options.get("profile_read"):
        entry = "profile_read.py"
    if options.get("matrix_status"):
        entry = "matrix_status.py"
    if options.get("alternate_diagnostics"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in ("multi_jetty_diagnostics.py", "hccl_baseline.py", "remote.py", "matrix.py"):
                archive.add(here / name, arcname=name)
        command = ('umask 077; d=$(mktemp -d /tmp/tilexr-multi-jetty-diagnostic.XXXXXXXX) && '
                   'tar -xzf - -C "$d" && python3 "$d/multi_jetty_diagnostics.py"')
        keepalive = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        proc = subprocess.run(args[:1] + keepalive + args[1:-2] + [command],
                              input=buffer.getvalue(), capture_output=True, timeout=820)
    elif options.get("hccl_baseline"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in ("hccl_baseline.py", "remote.py"):
                archive.add(here / name, arcname=name)
        remote = ('umask 077; d=$(mktemp -d /tmp/tilexr-hccl.XXXXXXXX) && '
                  'tar -xzf - -C "$d" && python3 "$d/hccl_baseline.py"')
        proc = subprocess.run(args[:-2] + [remote], input=buffer.getvalue(), capture_output=True, timeout=450)
    elif options.get("matrix"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in ("matrix.py", "remote.py", "qp_probe.cpp", "final_bench.cpp",
                         "ep8_modules.cpp", "native_send_probe.cpp", "ep8_concurrent.cpp", "final_probe.cpp"):
                archive.add(here / name, arcname=name)
        remote = ('umask 077; d=$(mktemp -d /tmp/tilexr-ep8-matrix.XXXXXXXX) && '
                  'tar -xzf - -C "$d" && python3 "$d/matrix.py"')
        if options.get("local_only"):
            remote += " local_only"
        elif options.get("smoke"):
            remote += " smoke"
        elif options.get("final_wait"):
            remote += " final_wait"
        elif options.get("device"):
            remote += " device"
        elif options.get("modules"):
            remote += " modules"
        elif options.get("profile"):
            remote += " profile"
        elif options.get("finish"):
            remote += " finish"
        elif options.get("remaining"):
            remote += " remaining"
        elif options.get("concurrent_remaining"):
            remote += " concurrent_remaining"
        elif options.get("concurrent_smoke"):
            remote += " concurrent_smoke"
        elif options.get("concurrent"):
            remote += " concurrent"
        proc = subprocess.run(args[:-2] + [remote], input=buffer.getvalue(), capture_output=True, timeout=1210)
    elif options.get("multi_jetty"):
        files = ("multi_jetty.py", "matrix.py", "multi_jetty_modules.cpp", "multi_jetty_qp.cpp")
        remote = ('umask 077; d=$(mktemp -d /tmp/tilexr-multi-jetty.XXXXXXXX) && '
                  'tar -xzf - -C "$d" && python3 "$d/multi_jetty.py"')
        if options.get("resume"):
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for name in (*files, "remote.py"):
                    archive.add(here / name, arcname=name)
            packed = buffer.getvalue()
            remote += " resume"
        else:
            from source_bundle import prepare
            packed, status = prepare(here / "remote.py", Path(temp),
                                     revision="4d37ae67cf2f6baa06b3efbe65c71649a6f24203",
                                     branch="codex/combine-ht-multi-jetty",
                                     support=[here / file for file in files])
            if packed is None:
                print(json.dumps(status, ensure_ascii=True, separators=(",", ":")))
                raise SystemExit(0)
        keepalive = ["-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
        proc = subprocess.run(args[:1] + keepalive + args[1:-2] + [remote],
                              input=packed, capture_output=True, timeout=760)
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
