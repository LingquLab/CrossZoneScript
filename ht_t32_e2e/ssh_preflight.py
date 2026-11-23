"""Check the existing green-side identities and read the pinned run's state."""
import json
import os
from pathlib import Path
import subprocess

host = "root@141.61.94.58"
options = ["-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
           "-o", "ConnectTimeout=8", "-o", "PasswordAuthentication=no",
           "-o", "KbdInteractiveAuthentication=no"]


def profiles():
    yield "configured", []
    roots = {Path.home(), *(Path(os.environ[key]) for key in ("USERPROFILE", "HOME") if os.environ.get(key))}
    seen = set()
    for key in ("id_ed25519_tilexr_sim", "id_ed25519", "id_rsa"):
        for root in sorted(roots):
            identity = root / ".ssh" / key
            if identity in seen:
                continue
            seen.add(identity)
            if identity.is_file() and identity.with_suffix(identity.suffix + ".pub").is_file():
                yield "existing_simulator_identity" if key.endswith("tilexr_sim") else "existing_default_identity", [
                    "-i", identity.as_posix(), "-o", "IdentitiesOnly=yes",
                ]


def diagnose(stderr):
    text = stderr.decode(errors="replace").lower()
    return next((name for phrase, name in (
        ("permission denied", "authentication_denied"),
        ("host key verification failed", "host_key_verification"),
        ("connection timed out", "connect_timeout"),
        ("connection refused", "connect_refused"),
        ("no route to host", "no_route"),
        ("agent refused operation", "agent_refused"),
    ) if phrase in text), "unclassified")


source = r"""
import json, re, subprocess
from pathlib import Path
case=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t32-combine-fp32-c4297a51')
row={'case_exists':case.is_dir()}
names=('run.json','task-result.json','e2e-summary.json')
for name in names:
    p=case/name
    if p.is_file():
        try: data=json.loads(p.read_text())
        except ValueError: continue
        keys=('status','kernel_sha256','returncode','guards','run','executed_sst','sst_simulated_microseconds',
              'checked','stage_error','first_mismatch','runner_exit_code','cleanup_error','private_container_removed')
        row[name]={key:data[key] for key in keys if key in data}
result=subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,timeout=8)
row['docker_ready']=result.returncode==0
print(json.dumps(row,separators=(',',':')))
"""


def check():
    result = {"attempts": []}
    try:
        config = subprocess.run(["ssh", "-G", host], capture_output=True, timeout=6)
        settings = {}
        if not config.returncode:
            for line in config.stdout.decode(errors="replace").splitlines():
                name, _, value = line.partition(" ")
                if name in ("controlpath", "identityagent"):
                    settings[name] = value
        result["multiplex_configured"] = settings.get("controlpath", "none").lower() != "none"
        if result["multiplex_configured"]:
            master = subprocess.run(["ssh", *options, "-O", "check", host], capture_output=True, timeout=6)
            result["multiplex_active"] = master.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        result["ssh_config_check"] = "unavailable"
    try:
        agent = subprocess.run(["ssh-add", "-l"], capture_output=True, timeout=5)
        result["agent_has_identity"] = agent.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        result["agent_has_identity"] = False
    for profile, extra in profiles():
        try:
            proc = subprocess.run(["ssh", *options, *extra, host, "python3", "-"], input=source.encode(),
                                  capture_output=True, timeout=22)
        except subprocess.TimeoutExpired:
            result["attempts"].append({"profile": profile, "reason": "ssh_command_timeout"})
            continue
        if not proc.returncode:
            result["authenticated"] = True
            result["profile"] = profile
            result["state"] = json.loads(proc.stdout)
            return result
        reason = diagnose(proc.stderr)
        result["attempts"].append({"profile": profile, "ssh_exit": proc.returncode, "reason": reason})
        if reason in ("connect_timeout", "connect_refused", "no_route", "host_key_verification"):
            break
    return dict(result, authenticated=False)


if __name__ == "__main__":
    print(json.dumps(check(), separators=(",", ":")))
