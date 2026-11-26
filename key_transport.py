from copy import deepcopy
from pathlib import Path
import json
import os
import shutil
import subprocess

NEW_NODE1 = "90.90.93.188"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def make_profile(runner, profile_path, output):
    profile = deepcopy(runner.load_profile(profile_path))
    source_inventory = Path(profile["cluster"]["inventory"])
    inventory = json.loads(source_inventory.read_text(encoding="utf-8-sig"))
    cluster = inventory["clusters"][profile["cluster"]["name"]]
    node1_name = profile["hosts"][1]["name"]
    node1 = next(row for row in cluster["hosts"] if row["name"] == node1_name)
    old = node1["address"]
    node1["address"] = NEW_NODE1
    if profile["network"].get("leader") == old:
        profile["network"]["leader"] = NEW_NODE1
    output.mkdir(exist_ok=True)
    local_inventory = output / "hosts-84-188.json"
    write_json(local_inventory, inventory)
    profile["cluster"]["inventory"] = str(local_inventory.resolve())
    write_json(output / "profile-84-188.json", profile)
    return profile


def install(runner, profile, fallback=None):
    tssh = runner.tssh
    cluster = tssh.find_cluster(tssh.load_clusters(Path(profile["cluster"]["inventory"])),
                                profile["cluster"]["name"])
    hosts = [next(host for host in cluster.hosts if host.name == item["name"])
             for item in profile["hosts"]]
    configured = profile.get("ssh", {}).get("executable", "ssh")
    windows_ssh = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
    executables = list(dict.fromkeys(str(path) for path in (Path(configured), windows_ssh,
                     Path(shutil.which("ssh") or "/nonexistent")) if path.is_file()))
    route = {}

    def parse(done, stdout, stderr, node):
        if done.returncode == 0:
            try:
                return json.loads(stdout.read_bytes())
            except ValueError:
                return {"status": "BLOCKED", "node": node, "reason": "remote_json_invalid"}
        err = stderr.read_text(encoding="utf-8", errors="replace").lower()
        reason = ("host_key" if "host key" in err else "permission_denied" if "permission denied" in err
                  else "connection_timeout" if "timed out" in err else "ssh_exit")
        return {"status": "BLOCKED", "node": node, "reason": reason, "exit_code": done.returncode}

    def invoke(exe, node, script, directory, label, timeout):
        stdout = directory / f"{label}-{node}.stdout"
        stderr = directory / f"{label}-{node}.stderr"
        user = cluster.user if node == 0 else "root"
        args = [exe, "-T", "-o", "BatchMode=yes", "-o", "PreferredAuthentications=publickey",
                "-o", "StrictHostKeyChecking=accept-new", "-o", "UpdateHostKeys=no", "-o", "ConnectTimeout=10",
                f"{user}@{hosts[node].address}", "python3 -"]
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                done = subprocess.run(args, input=script.encode("utf-8"), stdout=out,
                                      stderr=err, timeout=timeout)
            return parse(done, stdout, stderr, node)
        except (OSError, subprocess.SubprocessError) as error:
            return {"status": "BLOCKED", "node": node, "reason": "ssh_transport_failed",
                    "error_type": type(error).__name__}

    def transport(current_profile, node, script, directory, label, timeout):
        if node not in route:
            probe = "import json; print(json.dumps({'status':'PASS'}))"
            last = {"status": "BLOCKED", "node": node, "reason": "ssh_executable_missing"}
            for i, exe in enumerate(executables):
                last = invoke(exe, node, probe, directory, label + "-key" + str(i), 25)
                if last.get("status") == "PASS":
                    route[node] = exe
                    break
                if last.get("reason") == "host_key":
                    return last
            else:
                if fallback is None:
                    return last
                last = fallback(current_profile, node, probe, directory, label + "-retained-key", 25)
                if last.get("status") != "PASS":
                    return last
                route[node] = None
        if route[node] is None:
            return fallback(current_profile, node, script, directory, label, timeout)
        return invoke(route[node], node, script, directory, label, timeout)

    return transport
