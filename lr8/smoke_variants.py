"""Validate the independent CW and local7 kernel entries at 8 logical ranks."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--args-file", type=Path, required=True)
request = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
assert set(request) == {"revision"}
revision = request["revision"]
assert re.fullmatch("[0-9a-f]{40}", revision)
runner = Path(__file__).with_name("run.py")
failed = False
for name in ("urma_cw", "urma_lr8_p16_s16_local7", "urma_lr8_p16_s8_local7"):
    label = {"urma_cw": "cw", "urma_lr8_p16_s16_local7": "local7-s16",
             "urma_lr8_p16_s8_local7": "local7-s8"}[name]
    work = Path.cwd() / label
    work.mkdir()
    args = work / "args.json"
    args.write_text(json.dumps(dict(revision=revision, kernel_name=name,
                                    label=f"final-{revision[:8]}-{label}", cases=[[8, 128]],
                                    routing="with-clos", threads=8, batch_timeout=800)), encoding="utf-8")
    with (work / "stderr.log").open("wb") as errors:
        result = subprocess.run([sys.executable, str(runner), "--args-file", str(args)],
                                cwd=work, stderr=errors, stdout=subprocess.PIPE, timeout=850)
    (work / "summary.jsonl").write_bytes(result.stdout)
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    print(json.dumps(dict(kernel=name, revision=revision, status=result.returncode, result=rows)), flush=True)
    failed |= result.returncode != 0
raise SystemExit(int(failed))
