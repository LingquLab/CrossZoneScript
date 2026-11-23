"""Verify addressed SST output, all padding and each AIV status word."""
import hashlib
import json
from pathlib import Path
import struct
import sys

from decode import V310_DECODER_SHA256, reconstruct_die_output


def check(case):
    spec = json.loads((case / "manifest.json").read_text())
    guards = {}
    values = {}
    for name, region in spec["exports"].items():
        paths = [case / "bin" / f"david_0_die{die}_{name}.bin" for die in (1, 2)]
        d = reconstruct_die_output(paths, region["addr"], region["size"])
        end = 512 + region["payload_size"]
        values[name] = d[512:end]
        guards[name] = set(d[:512] + d[end:]) == {0xA5}
    expected = (case / "expected.bin").read_bytes()
    actual_words = struct.unpack(f"<{len(expected)//4}I", values["output"])
    expected_words = struct.unpack(f"<{len(expected)//4}I", expected)
    wrong = [i for i,(a,b) in enumerate(zip(actual_words,expected_words)) if a != b]
    raw_status = values["status"]
    statuses = [struct.unpack_from("<I",raw_status,32*b)[0] for b in range(spec["blocks"])]
    guards["status_padding"] = all(raw_status[i] == 0xA5 for i in range(len(raw_status)) if i%32 >= 4)
    result = dict(spec, status=statuses, status_ok=statuses == [spec["expected_status"]]*spec["blocks"],
                  guards=guards, checked=len(actual_words), mismatches=len(wrong),
                  output_untouched=all(v == 0x7FC00000 for v in actual_words),
                  mismatch_by_token=[sum(i//spec["hidden"] == t for i in wrong) for t in range(4)])
    if wrong:
        i = wrong[0]
        result["first"] = {"token":i//spec["hidden"],"h":i%spec["hidden"],
                           "expected":f"0x{expected_words[i]:08x}","actual":f"0x{actual_words[i]:08x}"}
    result.pop("exports")
    return result


if __name__ == "__main__":
    decoder = Path("/opt/sim/plugins/libDavinciSimulator.so")
    if hashlib.sha256(decoder.read_bytes()).hexdigest() != V310_DECODER_SHA256:
        raise ValueError("Unexpected V310 decoder hash")
    print(json.dumps(check(Path(sys.argv[1]))))
