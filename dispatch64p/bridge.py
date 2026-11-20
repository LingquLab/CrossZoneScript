import json
from pathlib import Path
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    step = args.get("step", "prepare")
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


if __name__ == "__main__":
    main()
