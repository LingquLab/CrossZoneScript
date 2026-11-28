"""Read the failed clone and look for existing TileXR checkout locations."""
from pathlib import Path
import json
import os
import re
import subprocess

home = Path.home()
checkout_candidates = []
for scope in (home, home / "work", home / "workspace", Path("/workspace"), Path("/data")):
    if not scope.is_dir():
        continue
    for p in scope.iterdir():
        if "tilexr" in p.name.lower() and (p / ".git").exists():
            git = subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            checkout_candidates.append((str(p), git.stdout.strip() if not git.returncode else None))
root = home / "tilexr-validation" / "ht-0b9c3322-ep8-1795841361"
msg = (root / "clone.log").read_text(errors="replace")
kinds = ("could not resolve", "connection reset", "connection timed out", "could not read username",
         "authentication failed", "repository not found", "empty reply", "ssl", "proxy", "failed to connect",
         "gnutls", "network is unreachable", "early eof", "http 403", "http 502")
result = dict(stage="inspect_clone", clone_signatures=[p for p in kinds if p in msg.lower()],
              clones=checkout_candidates[:15],
              proxy_variables=[n for n in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy") if n in os.environ],
              rootinfo=Path("/etc/hccl_rootinfo.json").is_file(),
              clone_last=re.sub(r"(?i)(https?://)[^/\s]*@", r"\1[redacted]@", msg.splitlines()[-1])[:220] if msg else "")
print("TILEXR_RESULT " + json.dumps(result, ensure_ascii=True, separators=(",", ":")))
