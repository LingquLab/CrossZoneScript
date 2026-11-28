"""Compare actual CCE argument vectors from the retained 16P qualification receipts."""

import json
import shlex
import subprocess

from lr8 import SSH


PAIRS = {
    "SPLIT_UDMA_WRITES": ("lr8-sim-split0.Kzvj0um0", "lr8-sim-split1.P9yM1g9z"),
    "SPLIT_SQ_FETCH": ("clos-sq-fetch0.5W2qwZRv", "clos-sq-fetch1.VJpqizCK"),
    "MTE_QUEUE_STATE": ("lr8-queue-sim-native-queue.TneszQLD", "lr8-queue-sim-split1.KF6UgMwQ"),
    "CQ_GM_DOORBELL": ("lr8-queue-sim-split1.KF6UgMwQ", "lr8-queue-sim-cq-gm.5ENLExDw"),
}
IDENTITY = ("compiler_sha256", "linker_sha256", "image_id", "cann_env_sha256", "ubus_sha256",
            "source_lock_sha256")


def capture():
    folders = dict.fromkeys(side for pair in PAIRS.values() for side in pair)
    paths = [f"/opt/tilexr-sst-08f501a0-{name}/16p-qualified/cmake-artifact.json" for name in folders]
    script = "import json, sys; from pathlib import Path; print(json.dumps([json.loads(Path(p).read_text()) for p in sys.argv[1:]]))"
    command = shlex.join(["python3", "-B", "-c", script, *paths])
    result = subprocess.run([*SSH, command], capture_output=True, timeout=44)
    if result.returncode:
        return {"ssh_exit": result.returncode}
    receipts = json.loads(result.stdout)
    assert len(receipts) == len(folders)
    rows = dict(zip(folders, receipts))
    output = {}
    for macro, (off, on) in PAIRS.items():
        baseline, candidate = rows[off], rows[on]
        prefix = "-DTILEXR_ALLTOALL_A5_SIM_" + macro + "="
        flags = [[flag for flag in row["commands"][0] if flag.startswith(prefix)]
                 for row in (baseline, candidate)]
        others = [[flag for flag in row["commands"][0] if not flag.startswith(prefix)]
                  for row in (baseline, candidate)]
        output[macro] = dict(effective=[baseline["features"][macro], candidate["features"][macro]],
                             arguments=flags, other_argv_equal=others[0] == others[1],
                             same_environment=all(baseline[key] == candidate[key] for key in IDENTITY))
    return output


if __name__ == "__main__":
    print(json.dumps(capture(), separators=(",", ":")))
