# Copyright (c) 2026 TileXR Project.
"""Run the requested ranks in order and emit one bounded timing receipt."""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys

import green


def main(args):
    ranks = args.pop("rank_batch")
    if not ranks or ranks != sorted(set(ranks)) or any(rank not in (8, 16, 32, 64) for rank in ranks):
        raise ValueError("invalid ordered rank batch")
    results = []
    for rank in ranks:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            green.main({**args, "ranks": rank})
        result = json.loads(buffer.getvalue())
        results.append(result)
        if not result["ok"]:
            break
    complete = len(results) == len(ranks) and all(result["ok"] for result in results)
    print(json.dumps(dict(batch=ranks, timings_complete=complete, cases=results), ensure_ascii=True))
    return 0 if complete else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    sys.exit(main(json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))))
