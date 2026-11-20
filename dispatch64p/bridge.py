import json
from pathlib import Path
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    step = args.get("step", "prepare")
    if step not in {"prepare", "prepare_ub", "run", "verify"}:
        raise ValueError("unknown Dispatch simulation step")
    filename = "prepare.py" if step == "prepare_ub" else step + ".py"
    implementation = Path(__file__).with_name(filename)
    command = [
        "ssh", "-T", "-i", str(Path.home() / ".ssh/deploy_ed25519"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1", "root@141.61.94.18",
        "docker exec -i simbox python3 -B -" + (" ub" if step == "prepare_ub" else ""),
    ]
    result = subprocess.run(
        command, input=implementation.read_bytes(), capture_output=True,
        timeout={"prepare": 285, "prepare_ub": 285, "run": 2670, "verify": 2630}[step],
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    print(json.dumps(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
