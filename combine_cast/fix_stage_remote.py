"""Check repair-library transfer and find the matching paths in the base V310 image."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path("/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54")
CASE = ROOT / "cast-v310-repair-0784b139"
IMAGE = "sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5"
PLATFORM = ROOT / "inter-server-native/t4-sst-mte-drain-count-cache-38d743f7"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


CASE.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(dir=CASE) as tmp:
    incoming = Path(tmp)
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
        seen = set()
        for entry in archive:
            assert entry.isfile() and entry.name in LIBRARIES and entry.name not in seen
            assert 0 < entry.size < 100 * 1024**2
            target = incoming / entry.name
            with target.open("xb") as stream:
                shutil.copyfileobj(archive.extractfile(entry), stream)
            assert digest(target) == LIBRARIES[entry.name], entry.name
            assert target.open("rb").read(6) == b"\x7fELF\x02\x01", entry.name
            seen.add(entry.name)
    assert seen == set(LIBRARIES)
    stage = CASE / "lib"
    if not stage.exists():
        shutil.move(incoming, stage)
    assert all(digest(stage / name) == sha for name, sha in LIBRARIES.items())

probe = """
import hashlib, json, os, sys
from pathlib import Path
libs=set(sys.argv[1:])
found={}
for root, dirs, files in os.walk('/opt/sim'):
    for name in libs.intersection(files):
        path=Path(root)/name
        if path.is_file():
            with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
            found[str(path)]=[str(path.resolve()),digest]
print(json.dumps(found,separators=(',',':')))
"""
check = subprocess.run(["docker", "run", "--rm", "--runtime=runc", "--network=none", "--cap-drop=ALL",
                        "--entrypoint", "python3", IMAGE, "-c", probe, *LIBRARIES], capture_output=True, timeout=90)
(CASE / "image-inspect.log").write_bytes(check.stderr)
found = json.loads(check.stdout) if check.returncode == 0 else {}
platform_lines = [line.strip()[:180] for line in (PLATFORM / "installed_model.py").read_text().splitlines()
                  if any(word in line for word in ("Davinci", ".so", "LD_LIBRARY", "sha256"))]
result = {"stage": CASE.name, "count": len(LIBRARIES), "find_exit": check.returncode,
          "paths": found, "platform": platform_lines[:8]}
(CASE / "staged.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, separators=(",", ":")))
