"""Recover selected traces from complete measured matrices without another device run."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess

from matrix import ROOT, download
from run import SSH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))
    ssh = SSH + [args["host"]]
    for campaign in args["campaigns"]:
        assert re.fullmatch(r"matrix-(reduce|send)-[0-9a-f]{12}", campaign)
        restore(ssh, campaign)


def restore(ssh, campaign):
    collector = Path(__file__).with_name("collect.py").read_text(encoding="utf-8")
    code = collector.split('if __name__ == "__main__":')[0] + '''
if __name__ == "__main__":
    directory = Path(sys.argv[1])
    if not (directory / "report.json").is_file():
        print(json.dumps({"stage": "waiting_for_report"}))
        sys.exit(3)
    output = directory / "selected-traces"
    if all((output / stage / "selection.json").is_file() for stage in ("min", "max", "near_mean")):
        selected = [json.loads((output / stage / "selection.json").read_text())
                    for stage in ("min", "max", "near_mean")]
        print(json.dumps({"stage": "trace_selection", "groups": 3, "ranks_per_group": 8,
                          "exact_round_slices": sum(row["scope"] == "selected_iteration"
                                                    for group in selected for row in group["ranks"])}))
    else:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="select-", dir=directory) as working:
            staging = Path(working) / "traces"
            export(directory, staging)
            if output.exists():
                output.rename(directory / (Path(working).name + "-previous"))
            staging.rename(output)
'''
    proc = subprocess.run(
        ssh + [f"python3 - {ROOT}/{shlex.quote(campaign)}"],
        input=code.encode("utf-8"), capture_output=True, timeout=300,
    )
    print(json.dumps({"stage": "trace_recovery", "campaign": campaign, "exit": proc.returncode}))
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if line.startswith('{"stage": '):
            print(line[:240])
    if proc.returncode == 0:
        download(ssh, campaign, {"phase": "matrix"})
    elif proc.returncode != 3:
        err = proc.stderr.decode("utf-8", errors="replace")
        error = re.search(r"^(ValueError|TypeError|KeyError|IndexError|OSError|FileExistsError|FileNotFoundError|RuntimeError|JSONDecodeError):", err, re.M)
        if error:
            print(json.dumps({"stage": "trace_recovery_error", "type": error.group(1)}))


if __name__ == "__main__":
    main()
