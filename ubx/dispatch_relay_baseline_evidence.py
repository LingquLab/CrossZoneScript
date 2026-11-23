"""Compare saved all-off benchmark evidence and AICore executable sections."""

import json
from pathlib import Path, PurePosixPath
import shlex
import sys

from dispatch_relay_setup import NAMESPACE, REUSE_NAMESPACE
from stage_sources import connect, run_remote

PROBE = r'''
from hashlib import sha256
import json
from pathlib import Path
import struct
import sys

home = Path(sys.argv[1])

def elf_hashes(path):
    data = path.read_bytes()
    entry = {"file_sha256": sha256(data).hexdigest()}
    if data[:4] != b"\x7fELF" or data[4] not in (1, 2) or data[5] not in (1, 2):
        return entry
    endian = "<" if data[5] == 1 else ">"
    elf64 = data[4] == 2
    hdr = struct.unpack_from(endian + ("16sHHIQQQIHHHHHH" if elf64 else "16sHHIIIIIHHHHHH"), data)
    offset, stride, count, string_idx = hdr[6], hdr[11], hdr[12], hdr[13]
    section_format = endian + ("IIQQQQIIQQ" if elf64 else "IIIIIIIIII")
    sections = [struct.unpack_from(section_format, data, offset + stride * i) for i in range(count)]
    if not sections:
        return entry
    string_table = sections[string_idx]
    strings = data[string_table[4]:string_table[4]+string_table[5]]
    code = {}
    for name_at, _, flags, _, start, size, *_ in sections:
        if flags & 4 and size:
            name = strings[name_at:].split(b"\0", 1)[0].decode("ascii", "replace")
            code[name] = {"bytes":size,"sha256":sha256(data[start:start+size]).hexdigest()}
    if code:
        entry["code_sections"] = code
    return entry

phases = {
    ("RELAY", "topk_relay_plan"): "relay_plan",
    ("RELAY", "relay_compute"): "relay_compute",
    ("RELAY", "relay_flag_to_mem_send_end"): "relay_flag_to_submit",
    ("INTRA_SEND", "intra_send_role"): "intra_send",
}
result = {}
for namespace in sys.argv[2:]:
    root = home / namespace
    revision = json.loads((root / "upstream/manifest.json").read_text())["revisions"]["tail-relay"]["commit"]
    programs = {}
    for mode in ("off", "on"):
        kernels = list((root / "installs" / f"tail-relay-base-{mode}" / "lib").rglob("tilexr_ep_ubx_kernel.o"))
        if len(kernels) == 1:
            programs[mode] = elf_hashes(kernels[0])
    rows = {}
    for bs in (64, 128, 32):
        for mode in ("off", "on"):
            report = json.loads((root / "runs" / f"tail-relay-base-dispatch-bs{bs}-max2-{mode}" / "timing.json").read_text())
            native = report["native_task_us"]["round_slowest_rank"]
            stats = dict(native={key:round(val, 4) for key,val in native.items()},
                         route_sha256=report["route_sha256"],
                         configured_max_tokens=report["max_tokens"], abi=report["abi"], backend=report["backend"],
                         stages={phases[item["role"],item["stage"]]:
                                 round(item["round_slowest_core_us"]["median"],4)
                                 for item in report.get("stages", []) if (item["role"],item["stage"]) in phases})
            rows[f"{bs}-{mode}"] = stats
    result[revision[:8]] = dict(kernels=programs, cases=rows)
print(json.dumps(result, separators=(",", ":")))
'''

if __name__ == "__main__":
    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    with connect(args["host"]) as client, client.open_sftp() as transfer:
        home = PurePosixPath(transfer.normalize(".")) / ".local/state/tilexr-ubx"
        command = "/usr/bin/python3 - " + " ".join(shlex.quote(value) for value in
                                                   (str(home), NAMESPACE, REUSE_NAMESPACE))
        code, output, _ = run_remote(client, command, timeout=45, input_text=PROBE)
        if code:
            raise SystemExit(code)
        print(output)
