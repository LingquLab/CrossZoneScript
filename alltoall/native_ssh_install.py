"""Run one Windows OpenSSH password prompt using a process-local named pipe."""

import os
from multiprocessing.connection import Listener
from pathlib import Path
import subprocess
import tempfile
import threading
import uuid


def install(command, password, target):
    client = Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe"
    install_args = [str(client), "-v", "-o", "BatchMode=no", "-o", "StrictHostKeyChecking=yes", "-o",
                    "PreferredAuthentications=password", "-o", "NumberOfPasswordPrompts=1", "-o",
                    "ConnectTimeout=12", target, command]
    with tempfile.TemporaryDirectory(prefix="tilexr-ssh-askpass-") as directory:
        helper = Path(directory) / "AskPass.exe"
        source = Path(__file__).with_name("native_ssh_prompt.cs")
        build = ("Add-Type -Path " + ps_quote(str(source)) + " -OutputType ConsoleApplication -OutputAssembly "
                 + ps_quote(str(helper)))
        compiled = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", build],
                                  capture_output=True, timeout=30)
        if compiled.returncode or not helper.is_file():
            return None, {"stage": "native_prompt_build", "exit": compiled.returncode}
        name = "tilexr-ssh-" + uuid.uuid4().hex
        address = "\\\\.\\pipe\\" + name
        supplied = threading.Event()
        with Listener(address, family="AF_PIPE") as listener:
            def one_prompt():
                try:
                    with listener.accept() as pipe:
                        pipe.send_bytes(password.encode("utf-8"))
                        supplied.set()
                except OSError:
                    pass

            threading.Thread(target=one_prompt, daemon=True).start()
            environment = os.environ.copy()
            environment.update(SSH_ASKPASS=str(helper), SSH_ASKPASS_REQUIRE="force", DISPLAY="tilexr",
                               TILEXR_SSH_PROMPT_PIPE=name, TILEXR_SSH_PROMPT_TARGET=target)
            result = subprocess.run(install_args, stdin=subprocess.DEVNULL, capture_output=True,
                                    env=environment, timeout=32)
        diagnostic = result.stderr.decode("utf-8", "replace")
        return result, {"stage": "native_password_install", "exit": result.returncode,
                        "prompt_supplied": supplied.is_set(), "denied": "Permission denied" in diagnostic,
                        "connected": "Remote protocol version" in diagnostic,
                        "authenticated": "Authenticated to" in diagnostic,
                        "ssh_timeout": "timed out" in diagnostic}


def ps_quote(value):
    return "'" + value.replace("'", "''") + "'"
