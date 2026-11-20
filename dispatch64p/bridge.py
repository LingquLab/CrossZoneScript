import json
from pathlib import Path
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    step = args.get("step", "prepare")
    if step not in {"prepare", "prepare_ub", "run", "verify", "run_ub", "verify_ub"}:
        raise ValueError("unknown Dispatch simulation step")
    operation = step.removesuffix("_ub")
    implementation = Path(__file__).with_name(operation + ".py")
    command = [
        "ssh", "-T", "-i", str(Path.home() / ".ssh/deploy_ed25519"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1", "root@141.61.94.18",
        "docker exec -i simbox python3 -B -" + (" ub" if step.endswith("_ub") else ""),
    ]
    result = subprocess.run(
        command, input=implementation.read_bytes(), capture_output=True,
        timeout={"prepare": 285, "run": 2670, "verify": 2630}[operation],
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    print(json.dumps(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
