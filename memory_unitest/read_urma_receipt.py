"""Summarize a retained Shared Jetty regression without running it again."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


REMOTE_REPORT = r"""
import collections
import json
from pathlib import Path
import re
import sys

bind = Path(sys.argv[1])
suite = bind / 'jetty_peers-suite'
report = {'suite_present': suite.is_dir()}
for name in ('execution.json', 'runner.log'):
    report[name] = (suite / name).is_file()
sim_logs = sorted((suite / 'runs').glob('*/sim.log'))
for log in sim_logs[-1:]:
    with log.open('rb') as stream:
        start = stream.read(32768).decode(errors='replace')
        stream.seek(max(0, log.stat().st_size - 32768))
        tail = stream.read().decode(errors='replace')
    cycles = [int(item) for item in re.findall(r'\b([0-9]+)\(cycle\)', tail)]
    entries = re.findall(r'\[Element: ([^\]]+)\]', tail)
    report['sim_log'] = {
        'bytes': log.stat().st_size, 'last_cycle': max(cycles, default=None),
        'tail_elements': collections.Counter(re.sub(r'\d+', '#', element) for element in entries).most_common(8),
        'terminal_messages': [line.strip()[:350] for line in tail.splitlines()
                              if any(tag in line for tag in ('FATAL:', 'Simulation is complete', 'TIMEOUT'))][-5:],
        'mentions_kernel': 'jetty_peers' in start or 'jetty_peers' in tail,
    }
if (suite / 'runner.log').is_file():
    with (suite / 'runner.log').open('rb') as stream:
        stream.seek(max(0, (suite / 'runner.log').stat().st_size - 8192))
        report['runner_tail'] = [line.strip()[:350] for line in stream.read().decode(errors='replace').splitlines()
                                 if any(tag in line.lower() for tag in ('timeout', 'timed out', 'exit_code', 'error'))][-5:]
files = [path for path in suite.rglob('*') if path.is_file()]
trace = [path for path in files
         if '/stat_trace/' not in path.as_posix()
         and re.search(r'cycle|instr|dump|aiv|v310|core|iss|su\.log', path.name, re.I)]
report['directories'] = collections.Counter(path.relative_to(suite).parts[0] for path in files).most_common(8)
report['trace_files'] = [(path.relative_to(suite).as_posix(), path.stat().st_size) for path in trace[:16]]
report['instruction_tails'] = {}
for path in trace[:16]:
    if path.suffix not in ('.txt', '.log', '.csv') or 'inst' not in path.name.lower():
        continue
    with path.open('rb') as stream:
        stream.seek(max(0, path.stat().st_size - 8192))
        lines = stream.read().decode(errors='replace').splitlines()
    keys = r'\b(?:pc|opcode|cycle|instruction|stall|wait_flag|pipe_barrier)\b'
    report['instruction_tails'][path.relative_to(suite).as_posix()] = [
        line.strip()[:240] for line in lines if re.search(keys, line, re.I)
    ][-3:]
print(json.dumps(report))
"""


def read_remote(bind):
    if not re.fullmatch(r"/opt/tilexr-urma-unitest\.[A-Za-z0-9]{8}", bind):
        raise ValueError("unexpected Shared Jetty bind directory")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-i",
               str(Path.home() / ".ssh/id_ed25519_server"), "root@141.61.94.30",
               shlex.join(["python3", "-B", "-c", REMOTE_REPORT, bind])]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        return {"read_exit_code": completed.returncode}
    return json.loads(completed.stdout)


def get_report(output):
    report = json.loads((output / "result.json").read_bytes())
    evidence = {key: report.get(key) for key in ("status", "failed_phase", "error", "elapsed_seconds")}
    evidence["phases"] = {key: value.get("status") for key, value in report["phases"].items()}
    transport = output / "run" / "transport.json"
    if transport.is_file():
        record = json.loads(transport.read_bytes())[0]
        tail = record.get("stdout", "").splitlines()
        events = re.findall(r'\[Element: ([^\]]+)\]-\[Sim: ([0-9]+)\(cycle\)\]: \[([A-Za-z0-9_:]+)\]',
                            record.get("stdout", ""))
        if events:
            evidence["last_cycle"] = max(int(cycle) for _, cycle, _ in events)
            evidence["tail_events"] = Counter(event for _, _, event in events).most_common(8)
        evidence["log_files"] = [line.partition("=")[2] for line in tail if line.startswith("LOG_TAIL=")]
        for line in tail:
            tag, _, value = line.partition("=")
            if tag in ("SST_FAILURE", "SST_RESULT"):
                result = json.loads(value)
                evidence[tag] = {key: result.get(key) for key in (
                    "error", "runner_returncode", "remaining_processes", "simulated_us"
                )}
        patterns = ("Unknown low level", "AllToAll oracle", "Segmentation fault",
                    "Fatal:", "Mismatch", "ERROR:", "Validation failed")
        lines = tail + record.get("stderr", "").splitlines()
        evidence["diagnostics"] = list(dict.fromkeys(
            line.strip()[:350] for line in lines if any(pattern in line for pattern in patterns)
        ))[-5:]
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", required=True, type=Path)
    config = json.loads(parser.parse_args().args_file.read_bytes())
    revisions = set(config["revisions"])
    expected_bind = config.get("bind")
    if not revisions or any(not re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions):
        parser.error("supply complete commit IDs")
    records = []
    workspaces = sorted(Path(tempfile.gettempdir()).glob("urma-unitest-regression-*"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    for work in workspaces[:80]:
        marker = work / "revision.stdout.log"
        if not marker.is_file():
            continue
        revision = marker.read_text().strip()
        if revision not in revisions:
            continue
        bind_path = work / "bind.stdout.log"
        bind = bind_path.read_text().strip() if bind_path.is_file() else None
        if expected_bind is not None and bind != expected_bind:
            continue
        output = work / "jetty_peers"
        row = dict(revision=revision, result_present=(output / "result.json").is_file(),
                   cleanup_receipt=(work / "cleanup.stdout.log").is_file())
        if row["result_present"]:
            row.update(get_report(output))
        if config.get("read_remote") is True and bind is not None:
            row["remote"] = read_remote(bind)
        records.append(row)
    print(json.dumps({"records": records}))


if __name__ == "__main__":
    main()
