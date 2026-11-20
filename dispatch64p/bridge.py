import json
from pathlib import Path
import subprocess


def main():
    implementation = Path(__file__).with_name("prepare.py")
    command = [
        "ssh", "-T", "-i", str(Path.home() / ".ssh/deploy_ed25519"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1", "root@141.61.94.18",
        "docker exec -i simbox python3 -B -",
    ]
    result = subprocess.run(
        command, input=implementation.read_bytes(), capture_output=True, timeout=285,
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    print(json.dumps(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
