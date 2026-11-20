import json
from pathlib import Path
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    step = args.get("step", "prepare")
    if step == "ab_prepare":
        return campaign_prepare()
    if step not in {"prepare", "prepare_ub", "run", "verify", "run_ub", "verify_ub", "run_verify_ub"}:
        raise ValueError("unknown Dispatch simulation step")
    operation = step.removesuffix("_ub")
    directory = Path(__file__).parent
    if operation == "run_verify":
        run_source = (directory / "run.py").read_text(encoding="utf-8")
        verify_source = (directory / "verify.py").read_text(encoding="utf-8")
        payload = (
            "import json\n"
            "runner = {'__name__': 'dispatch_run'}\n"
            f"exec({run_source!r}, runner)\n"
            "result = runner['run']()\n"
            "if result['status'] == 'OUTPUT_READY':\n"
            "    verifier = {'__name__': 'dispatch_verify'}\n"
            f"    exec({verify_source!r}, verifier)\n"
            "    try:\n"
            "        result = verifier['verify']()\n"
            "    except (OSError, ValueError, KeyError) as error:\n"
            "        result = {'status': 'VERIFICATION_FAILED', 'error': str(error)[:260], 'run': result}\n"
            "print(json.dumps(result))\n"
        ).encode()
    else:
        payload = (directory / (operation + ".py")).read_bytes()
    command = [
        "ssh", "-T", "-i", str(Path.home() / ".ssh/deploy_ed25519"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1", "root@141.61.94.18",
        "docker exec -i simbox python3 -B -" + (" ub" if step.endswith("_ub") else ""),
    ]
    result = subprocess.run(
        command, input=payload, capture_output=True,
        timeout={"prepare": 285, "run": 2670, "verify": 2630, "run_verify": 2740}[operation],
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    print(json.dumps(json.loads(result.stdout)))


def campaign_prepare():
    script = Path(__file__).with_name("ab_prepare.py")
    ssh = [
        "ssh", "-T", "-i", str(Path.home() / ".ssh/deploy_ed25519"),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10",
        "-o", "ConnectionAttempts=1", "root@141.61.94.18",
    ]
    identity = subprocess.run(
        ssh + ['docker inspect --format "{{.Id}} {{.Image}} {{.State.Running}}" simbox'],
        capture_output=True, text=True, timeout=20,
    )
    expected = (
        "abc76d686d045a7a404429d4ba6654491ffc4eebd9a3a218b835be57a91b99a3 "
        "sha256:c2cb1056c975f1e24805f7d1e21d6cdc60e9789fff1647fac5dae9b9d782af5f true"
    )
    if identity.returncode or identity.stdout.strip() != expected:
        print(json.dumps({"status": "BLOCKED", "reason": "verified simbox container identity changed"}))
        return
    result = subprocess.run(
        ssh + ["docker exec -i simbox python3 -B -"],
        input=script.read_bytes(), capture_output=True, timeout=580,
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    print(json.dumps(json.loads(result.stdout)))


if __name__ == "__main__":
    main()
