"""Stage the seven operator-provided V310 repair libraries on the green simulator host."""
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

SOURCE = Path("D:/project/仿真配置/修正cast问题/lib")
MANIFEST = {
    "libAccessSubsysV310.so": "b69a06c53cc8da928d2c04688aea52b17b4970055df3feaad94b32d83db64baf",
    "libAicoreSubsysV310.so": "0784b13937568563c64f816da940e6a4152b5b30dce649d5db5f4fb89bd40cce",
    "libDavinciSimulator.so": "aeacef679d4b732abbe394a4cfd5235d8a70d6188d7dfe238aef57330161e09c",
    "libDavinciSimulatorV310.so": "9864650dd22bc29648d7fabd36a34c2a72d7392ba4cdadf1d1f8ee13f6570be0",
    "libInterconnectSubsysV310.so": "913381af637f48e9b9f44ef12e9bdea0014f9fc497ebc6181ec6c471cbdef998",
    "libMemSubsysV310.so": "e5305ae29ffb791c5145f240daf7227f88873b793e5cde3aa89ff1e658515964",
    "libStarsSubsysV310.so": "eabe55c9ab3d4bcd420a29aa3a3b04866b869da3a8738056452ded3597276283",
}
argv = [
    "ssh", "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
    "-o", "BatchMode=no", "-o", "NumberOfPasswordPrompts=1",
    "-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no", HOST,
]
with tempfile.TemporaryFile() as spool:
    with tarfile.open(fileobj=spool, mode="w") as archive:
        for name, digest in MANIFEST.items():
            with (SOURCE / name).open("rb") as source:
                assert hashlib.file_digest(source, "sha256").hexdigest() == digest, name
            archive.add(SOURCE / name, arcname=name, recursive=False)
    spool.seek(0)
    remote = "LIBRARIES = " + repr(MANIFEST) + "\n" + (HERE / "fix_stage_remote.py").read_text()
    compile(remote, "<stage-cast-libraries>", "exec")
    with askpass_environment() as env:
        proc = subprocess.run(argv + ["python3 -c " + shlex.quote(remote)], stdin=spool,
                              capture_output=True, env=env, timeout=450)
if proc.returncode:
    Path("cast-stage-ssh.log").write_bytes(proc.stderr)
    print(json.dumps({"ssh_exit": proc.returncode, "stderr_bytes": len(proc.stderr)}))
    raise SystemExit(1)
print(proc.stdout.decode().strip())
