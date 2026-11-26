from pathlib import Path
import json
import re
import runpy
import time


def from_handbook(runner, profile):
    import paramiko

    data = Path("D:/project/UBX攻关/环境信息/实机手册.md").read_text(encoding="utf-8-sig")
    tssh = runner.tssh
    cluster = tssh.find_cluster(tssh.load_clusters(Path(profile["cluster"]["inventory"])), profile["cluster"]["name"])
    hosts = [next(host for host in cluster.hosts if host.name == entry["name"])
             for entry in profile["hosts"]]
    allowed = [host.address for host in hosts]
    helper = Path(runner.__file__).with_name("ubx_ssh_askpass.py")
    resolve = runpy.run_path(str(helper))["resolve_passwords"]

    def authenticate(address):
        try:
            return resolve(data, allowed)[address]
        except ValueError:
            start = re.search(r"^.*(?<![0-9.])" + re.escape(address) + r"(?![0-9.]).*$", data, re.M)
            if not start:
                raise ValueError("handbook has no section for configured node") from None
            current = []
            section = data[start.end():]
            end = re.search(r"^.*\b(?:\d{1,3}\.){3}\d{1,3}\b.*$", section, re.M)
            if end:
                section = section[:end.start()]
            for line in section.splitlines():
                entry = re.match(r"^([\w*\x60]{1,40})(?:\s*[:：=]\s*|\s+)(\S+)\s*$", line.strip().strip("|").strip())
                if not entry:
                    continue
                label, value = entry.groups()
                if re.search(r"旧|原|初始|默认|历史|BMC|管理口|镜像|容器", label):
                    continue
                if re.search(r"密码|password|passwd", label, re.I):
                    priority = 3 if re.search(r"当前|新|现用|修改后|更改后|更新后", label) else 2 if re.search(r"系统|登录|SSH|root", label, re.I) else 1
                    current.append((priority, value.strip(chr(96) + chr(34) + chr(39))))
            if not current:
                raise ValueError("handbook has no current password for configured node") from None
            best = max(priority for priority, _ in current)
            matching = {value for priority, value in current if priority == best}
            if len(matching) != 1:
                raise ValueError("handbook password entry is ambiguous") from None
            return matching.pop()

    def transport(current_profile, node, script, directory, label, timeout):
        host = hosts[node]
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        stdout_file = directory / f"{label}-{node}.stdout"
        stderr_file = directory / f"{label}-{node}.stderr"
        try:
            client.connect(host.address, username=cluster.user, password=authenticate(host.address),
                           timeout=12, banner_timeout=12, auth_timeout=12,
                           look_for_keys=False, allow_agent=False)
            incoming, output, errors = client.exec_command("python3 -", timeout=timeout)
            incoming.write(script)
            incoming.flush()
            incoming.channel.shutdown_write()
            channel = output.channel
            deadline = time.monotonic() + timeout
            status = None
            with stdout_file.open("wb") as out, stderr_file.open("wb") as err:
                while time.monotonic() < deadline:
                    if channel.recv_ready():
                        out.write(channel.recv(65536))
                    if channel.recv_stderr_ready():
                        err.write(channel.recv_stderr(65536))
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                        status = channel.recv_exit_status()
                        break
                    time.sleep(0.05)
            if status != 0:
                return {"status": "BLOCKED", "node": node, "reason": "remote_exit", "exit_code": status}
            return json.loads(stdout_file.read_bytes())
        except Exception as error:
            return {"status": "BLOCKED", "node": node, "reason": "ssh_transport_failed",
                    "error_type": type(error).__name__}
        finally:
            client.close()

    return transport
