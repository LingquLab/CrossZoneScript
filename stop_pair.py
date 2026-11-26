import inspect
import json
from pathlib import Path
import sys

from controller import REMOTE
from read_case import case_transport
from run_matrix import retryable_occupancy


def stop(directory, capture):
    import json
    import os
    from pathlib import Path
    import signal

    root = Path(directory)
    if (root / "run-result.json").is_file():
        return {"status": "already_exited"}
    children = json.loads((root / "children.json").read_text())
    for child in children:
        pid = child["pid"]
        proc = Path(f"/proc/{pid}")
        try:
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            command = (proc / "cmdline").read_bytes()
            if str(root / f"rank-{child['rank']}").encode() not in command or capture.encode() not in command:
                continue
            if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
                descriptor = os.pidfd_open(pid)
                try:
                    if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != stat[19]:
                        continue
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                finally:
                    os.close(descriptor)
            else:
                if int(stat[2]) != pid or (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != stat[19]:
                    continue
                os.kill(pid, signal.SIGTERM)
            return {"status": "signaled_one_own_rank"}
        except (FileNotFoundError, ProcessLookupError):
            continue
    return {"status": "no_matching_rank"}


def main(args):
    outcome, profile, run_id, capture, transport = case_transport(args["case"])
    if run_id != args["run_id"]:
        print(json.dumps({"status": "case_already_advanced"}))
        return
    directory = f"{REMOTE}/runs/{run_id}"
    early = outcome / "run-1.stdout"
    if args.get("awaiting_peer") and early.is_file():
        receipt = json.loads(early.read_text(encoding="utf-8"))
        if receipt.get("run_id") == run_id and receipt.get("node") == 1 and retryable_occupancy(receipt):
            command = ("import json\nfrom pathlib import Path\n"
                       f"root=Path({directory!r})\n"
                       "if (root/'started.json').is_file() and not (root/'run-result.json').is_file():\n"
                       " (root/'peer-failed.flag').touch()\n"
                       "print(json.dumps({'status':'notified_own_worker'}))\n")
            print(json.dumps(transport(profile, 0, command, outcome, "close-preflight-failure", 20)))
            return
    peer = ("import json\nfrom pathlib import Path\n"
            f"receipt=Path({directory!r})/'run-result.json'\n"
            "result=json.loads(receipt.read_text()) if receipt.is_file() else {}\n"
            "print(json.dumps({'failed': result.get('status') == 'FAIL' and result.get('network_restored') is True}))\n")
    if not transport(profile, 1, peer, outcome, "confirm-peer-exit", 20).get("failed"):
        print(json.dumps({"status": "peer_failure_unconfirmed"}))
        return
    command = "import json\n" + inspect.getsource(stop) + f"\nprint(json.dumps(stop({directory!r}, {capture!r})))\n"
    print(json.dumps(transport(profile, 0, command, outcome, "end-owned-rank", 20)))


if __name__ == "__main__":
    assert sys.argv[1] == "--args-file"
    main(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
