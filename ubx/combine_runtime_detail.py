"""Extract the detail from the actual Combine ACL error captured by the fixture."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shlex
import sys

from stage_sources import connect, run_remote


REMOTE = r'''
import json
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
error = re.compile(r'mte[123]|mte error|vec(?:tor)? (?:core|error)|aiv|aicore|ub address|exception|507035|error info|error code|kernel name|assert|ecc|out of (?:bound|range)|task_id|program counter', re.I)
hits = {}
for path in sorted(root.glob("rank-*/stderr.log")):
    text = path.read_bytes()[-150000:].decode(errors="replace")
    if "UBX_COMBINE_RUNTIME" not in text:
        continue
    block = text.split("UBX_COMBINE_RUNTIME", 1)[1].split("UBX_FAILURE", 1)[0]
    selected = [line.strip()[:300] for line in block.splitlines() if error.search(line)]
    hits[path.parent.name] = selected[-9:]
print(json.dumps({"runtime_error_ranks": len(hits), "examples": dict(list(hits.items())[:4])}, separators=(",", ":")))
'''


def run(host, ns, attempt):
    with connect(host) as client, client.open_sftp() as sftp:
        root = sftp.normalize(".") + "/.local/state/tilexr-ubx/" + ns + "/" + attempt
        code, output, _ = run_remote(client, "/usr/bin/python3 - " + shlex.quote(root),
                                      timeout=35, input_text=REMOTE)
        if code:
            raise RuntimeError(f"unable to read {host} runtime detail")
        print(json.dumps({"host": host, **json.loads(output)}, separators=(",", ":")))


if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]+", args[key]) for key in ("namespace", "attempt")):
        raise ValueError("invalid audit identity")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, host, args["namespace"], args["attempt"]) for host in args["hosts"]]
        for future in futures:
            future.result()
