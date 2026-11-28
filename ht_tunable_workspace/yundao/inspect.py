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

build_case = home / "tilexr-validation" / "ht-0b9c3322-ep8-1795841798"
log = (build_case / "build.log").read_text(errors="replace")
failures = [line.strip()[:260] for line in log.splitlines()
            if re.search(r"fatal error:| error:|undefined reference|cannot find|error\(s\) detected|not found", line, re.I)]
r = subprocess.run(["npu-smi", "info", "-t", "proc-mem"], capture_output=True, text=True, timeout=15)
smi = subprocess.run(["npu-smi", "info"], capture_output=True, text=True, timeout=15)
process_headers = []
section = False
process_lines = []
for line in smi.stdout.splitlines():
    if "Process id" in line and "Process memory" in line:
        section = True
        process_headers.append(line.strip()[:130])
    elif section and ("found" in line.lower() or "|" in line):
        if "No running processes" in line:
            process_lines.append(line.strip()[:90])
        elif re.search(r"\|\s*\d+\s+", line):
            process_lines.append(re.sub(r"[A-Za-z_/][-A-Za-z0-9_./]*", "name", line.strip())[:85])
result.update(build_first_errors=failures[:8], proc_mem_exit=r.returncode,
              proc_mem_summary=r.stdout.splitlines()[:3], inventory_process_headers=process_headers,
              inventory_process_lines=process_lines[:12])
print("TILEXR_RESULT " + json.dumps(result, ensure_ascii=True, separators=(",", ":")))


case = home / "tilexr-validation/ht-0b9c3322-ep8-1795842101"
rank_errors = {}
for rank in (0, 1):
    log = (case / f"rank-{rank}.log").read_text(errors="replace")
    rows = [re.sub(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b", "[ip]", line.strip())[:220]
            for line in log.splitlines()
            if re.search(r"error|failed|not support|ret=|permission", line, re.I)]
    rank_errors[rank] = {"first": rows[:12], "last": rows[-4:]}
print("TILEXR_RESULT " + json.dumps({"rank_errors": rank_errors}, ensure_ascii=True, separators=(",", ":")))
