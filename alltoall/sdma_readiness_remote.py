"""Read the 16P LR8 CommArgs and the component inventory inside the SST image."""

import json
from pathlib import Path
import struct
import subprocess


def check(configs):
    flags = []
    for index in range(16):
        data = (configs / f"input_bin/rank{index}/comm_args.bin").read_bytes()
        extra = struct.unpack_from("<I", data, 16)[0]
        workspace = struct.unpack_from("<Q", data, 529864)[0]
        flags.append((bool(extra & (1 << 11)), workspace != 0))
    active = [configs / "test_UBusSim.py", *(configs / "v310_config").glob("*.yml")]
    markers = sorted({p.name for p in active if p.is_file() and b"sdma" in p.read_bytes().lower()})
    listing = subprocess.run(["bash", "-lc", "source /opt/sim/cann-env.sh && source /opt/sim/sst-env.sh && "
                              "command -v sst-info >/dev/null && timeout 18 sst-info"],
                             capture_output=True, timeout=25)
    components = sum(b"sdma" in line.lower() for line in listing.stdout.splitlines())
    return dict(ranks=len(flags), sdma_flag=sum(flag for flag, _ in flags),
                nonnull_workspace=sum(pointer for _, pointer in flags), config_mentions=markers,
                sst_info_exit=listing.returncode, registered_sdma_mentions=components)


if __name__ == "__main__":
    print(json.dumps(check(Path("/work/16p-suite")), separators=(",", ":")))
