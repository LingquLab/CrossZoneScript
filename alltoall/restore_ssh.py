"""Restore the selected green host's SSH public key with a one-use password envelope."""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import paramiko


WORKTREE = Path("D:/workspace/TileXR-worktrees/alltoall-sst-08f501a0-lr8-split-compare")
FINGERPRINT = "N/5E0aTJZ5hayoFTZ/ShUQ8qOuUlm1SuJDidIlFDSGM"
CHALLENGE_PREFIX = "tilexr-alltoall-ssh-08f501a0-"
SSH = Path.home() / ".ssh"


def setup_context():
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=WORKTREE, timeout=20)
    assert revision.decode().strip() == "163fa57f8f8363d774096b57a5c17aa36d11cb93"
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=WORKTREE, timeout=20)
    sys.path.insert(0, str(WORKTREE))
    from tools.tssh import tssh

    cluster = tssh.find_cluster(tssh.load_clusters(WORKTREE / "tools/tssh/clusters.json"), "c01-b03")
    host, = (entry for entry in cluster.hosts if entry.name == "C01-B03-5")
    assert (cluster.user, host.address) == ("root", "141.61.94.30")
    key = tssh.load_public_key(SSH / "id_ed25519_server.pub")
    kind, contents = key.split()[:2]
    digest = hashlib.sha256(base64.b64decode(contents, validate=True)).digest()
    assert kind == "ssh-ed25519" and base64.b64encode(digest).decode().rstrip("=") == FINGERPRINT
    known_hosts = SSH / "known_hosts"
    assert paramiko.HostKeys(str(known_hosts)).lookup(host.address)
    return tssh, cluster, host, known_hosts, key


def prepare():
    setup_context()
    challenge = Path(tempfile.mkdtemp(prefix=CHALLENGE_PREFIX))
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    record = challenge / "key.pem"
    with record.open("xb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    public = key.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return {"challenge": challenge.name, "public_der": base64.b64encode(public).decode()}


def install(args):
    challenge_name = args["challenge"]
    assert re.fullmatch(CHALLENGE_PREFIX + r"[a-z0-9_]{8}", challenge_name)
    challenge = Path(tempfile.gettempdir()) / challenge_name
    record = (challenge / "key.pem").rename(challenge / "consumed.pem")
    try:
        assert time.time() - record.stat().st_mtime < 900
        key = serialization.load_pem_private_key(record.read_bytes(), password=None)
    finally:
        record.unlink(missing_ok=True)
        if challenge.is_dir():
            challenge.rmdir()
    tssh, cluster, host, known_hosts, public_key = setup_context()
    password = key.decrypt(base64.b64decode(args["envelope"], validate=True), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)).decode("utf-8")
    result = tssh.install_key_host(paramiko, known_hosts, cluster, host, public_key, password, 12, 12)
    del password
    if not result.succeeded:
        failure = (result.launch_error or "").lower()
        category = ("password_rejected" if "authentication" in failure else
                    "host_key_error" if "host key" in failure else "key_install_failed")
        return {"stage": category, "remote_exit": result.returncode}

    probe = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o",
                            "PreferredAuthentications=publickey", "-o", "ConnectTimeout=12", "-i",
                            str(SSH / "id_ed25519_server"), f"{cluster.user}@{host.address}", "id -u"],
                           capture_output=True, timeout=22)
    return {"stage": "verified" if probe.returncode == 0 and probe.stdout.strip() == b"0" else "public_key_probe_failed",
            "authorized_key": result.stdout.strip().decode("ascii") if result.stdout.strip() in
            {b"installed", b"already-present"} else "unknown", "ssh_exit": probe.returncode}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    try:
        assert args["phase"] in {"prepare", "install"}
        result = prepare() if args["phase"] == "prepare" else install(args)
    except (AssertionError, KeyError, OSError, ValueError, paramiko.SSHException, subprocess.SubprocessError) as error:
        result = {"stage": "challenge_or_preflight_failed", "error_class": type(error).__name__}
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
