"""Pass the original green-local Cast fixtures to the patched simulator runner."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "ht_t32_e2e"), str(HERE.parent / "alltoall")]
from green_auth import askpass_environment
from ssh_transport import HOST

PATCH = Path("D:/project/仿真配置/修正cast问题")
cases = next(path.parent for path in PATCH.rglob("cases.json") if (path.parent / "cases").is_dir())
expected = {name: sha for sha, name in (line.split(maxsplit=1) for line in
                                       (HERE / "fix-case-sha256.txt").read_text().splitlines())}
argv = [
    "ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
    "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1", "-o",
    "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST,
]
options = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ["--args-file"] else {}
with tempfile.TemporaryFile() as spool:
    with tarfile.open(fileobj=spool, mode="w:gz", compresslevel=3) as archive:
        for name, sha in expected.items():
            path = cases / name
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha, name
            archive.add(path, arcname=name, recursive=False)
    spool.seek(0)
    source = "INPUTS = " + repr(expected) + "\nOPTIONS = " + repr(options) + "\n"
    source += (HERE / "fix_run_remote.py").read_text()
    compile(source, "<cast-regression-remote>", "exec")
    with askpass_environment() as env:
        proc = subprocess.run(argv + ["python3 -c " + shlex.quote(source)], stdin=spool,
                              capture_output=True, env=env, timeout=1700)
if proc.returncode:
    Path("cast-regression-ssh.log").write_bytes(proc.stderr)
    print(json.dumps({"ssh_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
print(proc.stdout.decode().strip())
