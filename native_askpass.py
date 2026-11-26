from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile

from transport_current import load_auth

PROGRAM = r'''
using System;
using System.IO;
using System.Text;
internal class AskPass {
    public static int Main(string[] args) {
        string host = Environment.GetEnvironmentVariable("UBX_ASKPASS_HOST");
        string password = Environment.GetEnvironmentVariable("UBX_ASKPASS_INPUT");
        if (args.Length != 1 || String.IsNullOrEmpty(host) || String.IsNullOrEmpty(password)
            || args[0].IndexOf(host, StringComparison.OrdinalIgnoreCase) < 0
            || args[0].IndexOf("password", StringComparison.OrdinalIgnoreCase) < 0)
            return 2;
        Console.OutputEncoding = new UTF8Encoding(false);
        Console.WriteLine(File.ReadAllText(password, Encoding.UTF8));
        return 0;
    }
}
'''


def native_transport(runner, profile, output):
    cluster, hosts, authenticate = load_auth(runner, profile)
    helpers = output / "native-ssh"
    helpers.mkdir(exist_ok=True)
    exe = helpers / "askpass.exe"
    if not exe.is_file():
        root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET"
        candidates = [root / flavor / "v4.0.30319/csc.exe" for flavor in ("Framework64", "Framework")]
        compiler = next((path for path in candidates if path.is_file()), None)
        if compiler is None:
            raise FileNotFoundError("Windows C# compiler unavailable")
        cpp = helpers / "askpass.cs"
        cpp.write_text(PROGRAM, encoding="ascii")
        with (helpers / "compile.log").open("wb") as log:
            subprocess.run([str(compiler), "/nologo", f"/out:{exe}", str(cpp)],
                           stdout=log, stderr=subprocess.STDOUT, timeout=30, check=True)
    configured = profile["ssh"].get("executable", "ssh")
    ssh = next((str(path) for path in (Path("C:/Program Files/Git/usr/bin/ssh.exe"), Path(configured))
                if path.is_file()), shutil.which(configured) or shutil.which("ssh"))
    assert ssh

    def transport(current_profile, node, script, directory, label, timeout):
        host = hosts[node]
        stderr_path = directory / f"{label}-{node}.stderr"
        stdout_path = directory / f"{label}-{node}.stdout"
        try:
            secret = authenticate(host.address)
            with tempfile.TemporaryDirectory(prefix="ubx-ssh-") as temp:
                file = Path(temp) / "input"
                file.write_text(secret, encoding="utf-8")
                env = dict(os.environ, SSH_ASKPASS=exe.as_posix(), SSH_ASKPASS_REQUIRE="force",
                           DISPLAY="ubx:ssh", UBX_ASKPASS_INPUT=file.as_posix(), UBX_ASKPASS_HOST=host.address)
                args = [ssh, "-T", "-o", "BatchMode=no", "-o", "PreferredAuthentications=password",
                        "-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1",
                        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12",
                        f"{cluster.user}@{host.address}", "python3 -"]
                with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                    result = subprocess.run(args, input=script.encode("utf-8"), env=env,
                                            stdout=out, stderr=err, timeout=timeout)
            if result.returncode:
                err = stderr_path.read_text(encoding="utf-8", errors="replace")
                reason = ("host_key" if "Host key" in err else "permission_denied" if "Permission denied" in err
                          else "password_helper" if "askpass" in err.lower() else "ssh_exit")
                return {"status": "BLOCKED", "node": node, "reason": reason, "exit_code": result.returncode}
            return json.loads(stdout_path.read_bytes())
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return {"status": "BLOCKED", "node": node, "reason": "ssh_transport_failed",
                    "error_type": type(error).__name__}

    return transport
