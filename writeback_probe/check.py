"""Check cast bytes at the UB boundary and after each GM copy."""
import hashlib
import json
from pathlib import Path
import struct
import sys

from decode import V310_DECODER_SHA256, reconstruct_die_output


def check(case):
    spec = json.loads((case/"manifest.json").read_text())
    reference = (case/"control.bin").read_bytes()
    result = {}
    for field, layout in spec["exports"].items():
        paths = [case/"bin"/f"david_0_die{die}_{field}.bin" for die in (1,2)]
        data = reconstruct_die_output(paths, layout["addr"], layout["size"])
        end = 512 + layout["payload_size"]
        result[field+"_guards"] = set(data[:512] + data[end:]) == {0xA5}
        for offset, name in enumerate(layout["regions"]):
            segment = data[512+offset*32:512+(offset+1)*32]
            pairs = list(zip(struct.unpack("<16H", reference),struct.unpack("<16H", segment)))
            errors = [[i, f"0x{want:04x}", f"0x{got:04x}"] for i,(want,got) in enumerate(pairs) if want!=got]
            result[name] = dict(errors=len(errors), checked=len(pairs), first=errors[:2])
    return result


if __name__ == "__main__":
    decoder = Path("/opt/sim/plugins/libDavinciSimulator.so")
    assert hashlib.sha256(decoder.read_bytes()).hexdigest() == V310_DECODER_SHA256
    print(json.dumps(check(Path(sys.argv[1]))))
