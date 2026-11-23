"""Read exact build and loader facts for retained failing and passing cases."""
import hashlib
import json
from pathlib import Path
import re
import socket
import sys

ROOT = Path("/var/tmp/tilexr-combine-ht-7626e689-38e9-468b-9a82-63673422e9d3-v11")
OLD = Path("/var/tmp/tilexr-combine-ht-05d1d635-d6d7-4dd3-af34-421e32bb209c-v11")
CASES = ((OLD, "mte-interleave-8"), (ROOT, "mte-scalar_pointer_runtime64-17"),
         (ROOT, "mte-scalar_store_post_runtime_vector-20"))
FLAGS = ("--cce-aicore-arch=dav-c310-vec", "--cce-aicore-only", "--cce-auto-sync",
         "-cce-aicore-stack-size=0x8000", "-cce-aicore-function-stack-size=0x8000",
         "-cce-aicore-record-overflow=true", "-cce-aicore-addr-transform",
         "-cce-aicore-dcci-insert-for-scalar=false")


def yaml_number(text, name):
    match = re.search(r"(?m)^\s+" + re.escape(name) + r":\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$", text)
    return int(match[1], 0) if match else None


def inspect(root, name):
    folder = root / name
    result = json.loads((folder / "trial-result.json").read_text())
    binary = folder / "send.o"
    assert binary.stat().st_size < 64 * 1024 * 1024
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert digest == result["object_sha256"], "object_does_not_match_saved_result"
    command = json.loads((folder / "build.json").read_text())[0]
    helper = (folder / "mte_state_generated.h").read_text()
    load = helper.split("inline uint32_t SimulatorLoad(", 1)[1].split("inline void SimulatorStore(", 1)[0]
    match = re.search(r"DataCopyExtParams\{1U,\s*(\d+)U", load)
    header = (folder / "combine_ht_kernel.h").read_text()
    common = (folder / "combine_ht.h").read_text()
    fields = re.search(r"kBackwardFields\s*=\s*(\d+)", common)
    workload = (folder / "capture/workload.yml").read_text()
    rank = re.search(r"(?ms)^  chip0:\n(.*?)(?=^  chip1:|\Z)", workload)
    assert rank, "rank0_workload_not_found"
    rank = rank[1]
    bin_block = re.search(r"(?ms)^    bin_param:\n(.*?)(?=^    \S|\Z)", rank)
    assert bin_block, "binary_binding_not_found"
    status_stride = 4 if "args.status) + core, statusUb, 1U" in header else None
    return {"case": name, "object_sha256": digest, "object_bytes": binary.stat().st_size,
            "compiler_flags": {flag: flag in command for flag in FLAGS},
            "state_read_bytes": int(match[1]) if match else None,
            "state_read_disables_l2": "CACHE_MODE_DISABLE" in load,
            "status_gm_slot_bytes": status_stride,
            "status_published_by_mte": "NativeStore(reinterpret_cast<__gm__ uint32_t *>(args.status)" in header,
            "backward_record_bytes": int(fields[1]) * 8 if fields else None,
            "native_load_disables_l2": "CACHE_MODE_DISABLE" in header.split("inline void NativeLoad(", 1)[1].split("inline void NativeStore(", 1)[0],
            "ub_base_addr": yaml_number(rank, "ub_base_addr"),
            "stack_base_addr": yaml_number(rank, "stack_base_addr"),
            "bin_parameter_bytes": yaml_number(bin_block[1], "size"),
            "stop_boundary": result.get("stop_boundary"), "saved_case_ok": result["ok"],
            "saved_simulator_returncode": result.get("returncode")}


def main():
    assert socket.gethostname().split(".")[0] == sys.argv[1] == "host-94-26"
    output = {"diagnostic_only": True, "simulation_started": False,
              "reference_commit": "80c738f5489bcdd04ed0d66f9fd3096484176ae4",
              "reference_compatibility_script": "e84b732bb50114d478aaf9ec3736fc6f47959cc5",
              "reference_documented_state_bytes": 4, "reference_status_slot_bytes": 32,
              "reference_padded_binary_bytes": 10485760,
              "cases": [inspect(root, name) for root, name in CASES]}
    print(json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    main()
