"""Check the approved model image and the retained LR8 loader for SDMA inputs."""

import json
from pathlib import Path
import shlex
import subprocess

from lr8 import IMAGE, SSH


BIND = "/opt/tilexr-sst-08f501a0-lr8-sim-split1.P9yM1g9z"


if __name__ == "__main__":
    source = Path(__file__).with_name("sdma_readiness_remote.py").read_bytes()
    run = ["docker", "run", "--rm", "-i", "--network", "none", "--runtime=ascend", "--mount",
           f"type=bind,src={BIND},dst=/work,readonly", IMAGE, "python3", "-B", "-"]
    result = subprocess.run([*SSH, shlex.join(run)], input=source, capture_output=True, timeout=65)
    if result.returncode:
        print(json.dumps(dict(exit=result.returncode, ssh_denied=b"Permission denied" in result.stderr)))
    else:
        print(result.stdout.decode("utf-8").strip())
