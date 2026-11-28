"""Continue 16P on an existing LR8 build after a transport interruption."""

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from lr8 import ENV, IMAGE, REPO, RESULTS, REVISION, SSH, UBUS, RunFailed
from lr8 import git_output, qualify, run, run_case
from local_receipt import LR8


OBJECTS = {
    "sim-split0": "d6d46aab93e367880b6b398c05b0d58679f7735405fbbd0ee19cda5dd14e5a7d",
    "sim-split1": "bacf628e1b1e5e0c82f4a3cf797b3998c83f3ae3899237b20204b88807d96e6f",
}
OBJECT = "/work/build/src/collectives/sst/tilexr_alltoall_urma_lr8_kernel.o"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--args-file", type=Path, required=True)
    side = json.loads(parser.parse_args().args_file.read_text(encoding="utf-8"))["side"]
    initial = RESULTS / LR8[side]
    before = json.loads((initial / "handoff.json").read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix=f"alltoall-sst-lr8-resume-{side}-", dir=RESULTS))
    bind, container = before["bind"], before["container_id"]
    state = dict(variant=side, logs=str(work), bind=bind, container_id=container, revision=REVISION)
    payload = Path(__file__).with_name("lr8_summary.py").read_bytes()

    def remote(args, label, timeout=45, data=None):
        state["stage"] = label
        return run([*SSH, shlex.join(args)], work, work / (label + ".log"), timeout, data)

    try:
        state["stage"] = "prerequisites"
        assert before["revision"] == REVISION and before["image_id"] == IMAGE
        assert re.fullmatch(r"/opt/tilexr-sst-08f501a0-lr8-" + side + r"\.[A-Za-z0-9]{8}", bind)
        assert re.fullmatch(r"[0-9a-f]{64}", container)
        assert git_output(["rev-parse", "HEAD"], REPO).decode().strip() == REVISION
        assert not git_output(["status", "--porcelain"], REPO).strip()
        identity = remote(["docker", "inspect", "--format",
                           '{{.Id}} {{.Image}} {{.State.Running}} '
                           '{{range .Mounts}}{{if eq .Destination "/work"}}{{.Source}}{{end}}{{end}}',
                           container], "container-identity")
        assert identity == f"{container} {IMAGE} true {bind}"
        values = remote(["docker", "exec", container, "sha256sum", "/work/source-lock/SOURCE_LOCK.json",
                         ENV, UBUS, OBJECT], "fingerprints")
        source, cann, ubus, binary = (line.split()[0] for line in values.splitlines())
        assert source == "a193e5420dae380964acaf86bc56ffd2aa752332c97f561ae7089dc6859410c6"
        assert binary == OBJECTS[side]
        evidence = remote(["docker", "exec", "-i", container, "python3", "-B", "-", "/work", "8"],
                          "8p-summary", 100, payload)
        state["8p"] = json.loads(evidence)
        remote(["test", "!", "-e", bind + "/16p-qualified"], "16p-new-qualification")
        remote(["test", "!", "-e", bind + "/16p-suite"], "16p-new-suite")
        hostname = remote(["hostname", "-s"], "hostname")
        state["stage"] = "16p-qualification"
        stage, config = qualify(work, side, Path(bind).name, container, hostname, (source, cann, ubus), 16)
        state["stage"] = "16p-unitest"
        state["16p"] = run_case(stage, config, 16)
        evidence = remote(["docker", "exec", "-i", container, "python3", "-B", "-", "/work", "16"],
                          "16p-summary", 100, payload)
        state["16p"]["remote"] = json.loads(evidence)
        remote(["docker", "rm", "-f", container], "cleanup", 70)
        state["stage"] = "finished"
    except (AssertionError, OSError, RunFailed, ValueError, subprocess.SubprocessError) as error:
        state["error_class"] = type(error).__name__
        if isinstance(error, RunFailed):
            state["phase_exit"] = error.args[1]
    (work / "handoff.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, separators=(",", ":")))


if __name__ == "__main__":
    main()
