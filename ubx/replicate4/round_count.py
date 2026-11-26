"""Use the pinned branch's clock verifier with a 10 + 50 round schedule."""
from pathlib import Path
import types


def load_clock_results(source):
    replacements = {
        "range(40)": "range(60)",
        "len(targets) == 40": "len(targets) == 60",
        "range(30)": "range(50)",
        "result['measured'] == 30": "result['measured'] == 50",
        "len(rows) == 40": "len(rows) == 60",
        "trace['metadata']['round'] == 39": "trace['metadata']['round'] == 59",
        "len(values) == 53760": "len(values) == 89600",
        "operator_revision='df260088c293ab86c0b2be716741b28c2b4cd839'": "operator_revision=revision",
        "measured=30": "measured=50",
        "verified_rounds_per_rank=40": "verified_rounds_per_rank=60",
        "last_trace_round=39": "last_trace_round=59",
        "rounds10-39": "rounds10-59",
    }
    for old, new in replacements.items():
        expected = 2 if old in {"len(rows) == 40", "range(40)"} else 1
        if source.count(old) != expected:
            raise ValueError(f"Unexpected clock_results.py expression {old!r}")
        source = source.replace(old, new)
    module = types.ModuleType("clock_results")
    exec(compile(source, "clock_results_60.py", "exec"), module.__dict__)
    return module


if __name__ == "__main__":
    print(load_clock_results(Path(__import__("sys").argv[1]).read_text()).__name__)
