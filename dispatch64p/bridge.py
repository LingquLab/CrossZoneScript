import json
from pathlib import Path
import subprocess
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text())
    step = args.get("step", "prepare")
    if step in {"ab_prepare", "ab_run", "ab_status", "late_wait", "late_wait_status"}:
        return campaign_step(step)
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


def campaign_step(step):
    directory = Path(__file__).parent
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
    if step == "late_wait":
        modules = {name: (directory / (name + ".py")).read_text(encoding="utf-8")
                   for name in ("run", "verify", "ab_prepare", "ab_run", "late_wait")}
        payload = (
            "RUNNER = {'__name__': 'dispatch_run'}\n"
            f"exec({modules['run']!r}, RUNNER)\n"
            "VERIFY = {'__name__': 'dispatch_verify'}\n"
            f"exec({modules['verify']!r}, VERIFY)\n"
            "PREP = {'__name__': 'dispatch_prepare'}\n"
            f"exec({modules['ab_prepare']!r}, PREP)\n"
            "CAMPAIGN = {'__name__': 'dispatch_campaign', 'RUNNER': RUNNER, 'VERIFY': VERIFY}\n"
            f"exec({modules['ab_run']!r}, CAMPAIGN)\n"
            f"exec({modules['late_wait']!r})\n"
        ).encode()
    elif step == "late_wait_status":
        payload = (
            "import json\n"
            "from pathlib import Path\n"
            "p = Path('/opt/sim/simulator/Testcases/dispatch_64p_top6_late_wait_20261123_01')\n"
            "names = ('build-receipt.json', 'campaign-status.json')\n"
            "print(json.dumps({name: json.loads((p / name).read_text()) for name in names if (p / name).is_file()}))\n"
        ).encode()
    elif step == "ab_run":
        modules = {name: (directory / (name + ".py")).read_text(encoding="utf-8")
                   for name in ("run", "verify", "ab_run")}
        payload = (
            "RUNNER = {'__name__': 'dispatch_run'}\n"
            f"exec({modules['run']!r}, RUNNER)\n"
            "VERIFY = {'__name__': 'dispatch_verify'}\n"
            f"exec({modules['verify']!r}, VERIFY)\n"
            f"exec({modules['ab_run']!r})\n"
        ).encode()
    elif step == "ab_status":
        payload = (
            "from pathlib import Path\n"
            "p = Path('/opt/sim/simulator/Testcases/dispatch_64p_top6_profile_ab_20261120_01/campaign-status.json')\n"
            "print(p.read_text() if p.exists() else '{\"status\": \"NO_CAMPAIGN_RECEIPT\"}')\n"
        ).encode()
    else:
        payload = (directory / "ab_prepare.py").read_bytes()
    container = expected.split(maxsplit=1)[0]
    result = subprocess.run(
        ssh + [f"docker exec -i {container} python3 -B -"],
        input=payload, capture_output=True,
        timeout={"ab_prepare": 580, "ab_run": 8000, "ab_status": 40,
                 "late_wait": 4800, "late_wait_status": 40}[step],
    )
    if result.returncode:
        print(json.dumps({"phase": "transport", "ssh_exit": result.returncode}))
        raise SystemExit(result.returncode)
    summary = json.loads(result.stdout)
    for row in summary.get("rounds", []):
        modules = row.get("module_stats", {})
        for values in modules if isinstance(modules, list) else modules.values():
            if "slowest" in values:
                values.setdefault("slowest_count", len(values["slowest"]))
                values["slowest"] = values["slowest"][:8]
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
