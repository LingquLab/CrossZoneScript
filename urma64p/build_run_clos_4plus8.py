"""Build a pinned Clos 4+8 candidate before running its SST cases."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    scripts = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="urma16p-4plus8-") as temp:
        path = Path(temp) / "args.json"
        path.write_text(json.dumps(dict(revision=args["revision"], variant="clos-4plus8")))
        result = subprocess.run([sys.executable, str(scripts / "build.py"), "--args-file", str(path)],
                                stdout=subprocess.PIPE, text=True, timeout=640)
        build = None
        for line in result.stdout.splitlines():
            row = json.loads(line)
            print(json.dumps(row), flush=True)
            if row.get("stage") == "compile" and row.get("passed") and row["revision"] == args["revision"]:
                build = row["run_dir"]
        if result.returncode or build is None:
            return result.returncode or 1
        path.write_text(json.dumps(dict(args, build=build)))
        return subprocess.run([sys.executable, str(scripts / "run_clos_4plus8.py"), "--args-file", str(path)],
                              timeout=args["timeout"] + 450).returncode


if __name__ == "__main__":
    raise SystemExit(main())
