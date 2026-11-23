"""Supply the existing green-local SSH password through OpenSSH askpass."""
from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import sys
import tempfile


@contextmanager
def askpass_environment():
    directory = Path("D:/project/仿真配置")
    note = next((directory / name for name in ("仿真系统环境信息.md", "仿真系统环境信息")
                 if (directory / name).is_file()), None)
    if note is None:
        raise FileNotFoundError
    with tempfile.TemporaryDirectory(prefix="tilexr-ssh-") as temporary:
        root = Path(temporary)
        reader = root / "read_local_auth.py"
        helper = root / "askpass.sh"
        reader.write_text(
            "from pathlib import Path\nimport re,sys\n"
            "raw=Path(sys.argv[1]).read_bytes()\n"
            "try: text=raw.decode('utf-8-sig')\n"
            "except UnicodeDecodeError: text=raw.decode('gb18030')\n"
            "match=re.search(r'密码更新[：:]\\s*([^\\r\\n]+)',text)\n"
            "if match is None or not match.group(1).strip(): sys.exit(1)\n"
            "sys.stdout.write(match.group(1).strip()+'\\n')\n",
            encoding="utf-8",
        )
        argv = (Path(sys.executable).as_posix(), reader.as_posix(), note.as_posix())
        helper.write_text("#!/bin/sh\nexec " + shlex.join(argv) + "\n", encoding="utf-8", newline="\n")
        helper.chmod(0o700)
        environment = os.environ.copy()
        environment.update(SSH_ASKPASS=helper.as_posix(), SSH_ASKPASS_REQUIRE="force", DISPLAY="cross-zone:0")
        yield environment
