"""Read the local-receive benchmark status and bounded diagnostics."""
import argparse
import json
from pathlib import Path

from disjoint_routes_status import main
from retest_local_receive import REVISION, TASK, WORK

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    options = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    main(task=TASK, revision=REVISION, work=WORK, iteration=options["iteration"])
