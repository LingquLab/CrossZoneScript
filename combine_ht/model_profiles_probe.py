"""Locate installed model profiles and bind the published ELF's fault to its queue."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

REMOTE = r'''
import json,pathlib,struct,subprocess
root=pathlib.Path("/opt/sim/simulator/v310_deployment")
out={"read_only":True,"configurations":[],"queue_fault_matches":[]}
for path in sorted(root.rglob("davidV100_simulator_config.yml"))[:24]:
    out["configurations"].append(str(path.relative_to(root)))
out["config_directories"]=[str(path.relative_to(root)) for path in sorted((root/"config").glob("*")) if path.is_dir()][:24]
fault=563912026198528
data=pathlib.Path("/work/diagnostics/reference-fixture-model-a1/shared/input_bin/rank08/native_sq_ctx.bin").read_bytes()
for gateway in range(1,8):
    peer=8+gateway
    doorbell=struct.unpack_from("<Q",data,(peer*32+gateway)*80+48)[0]
    if doorbell*8==fault:
        out["queue_fault_matches"].append({"rank":8,"peer":peer,"logical_qp":gateway,"doorbell":hex(doorbell),"fault_address":hex(fault),"factor":8})
p=subprocess.run(["nm","-DC","--defined-only","/opt/sim/plugins/libDavinciSimulatorV310.so"],capture_output=True,text=True,timeout=30)
assert p.returncode==0,"model_symbol_query_failed"
out["pv_model_symbols"]=sorted({line.split(" ",2)[-1] for line in p.stdout.splitlines() if any(word in line for word in ("ScalarUnitPv::ScalarUnitPv(","LsuPv::LsuPv(","ScalarUnitL0::ScalarUnitL0(","ScalarUnitL1::ScalarUnitL1("))})[:8]
print(json.dumps(out))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-file", type=Path, required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding="utf-8")) == {}
    key = Path(os.environ["USERPROFILE"]) / ".ssh/id_ed25519"
    argv = ["ssh", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12", "root@141.61.94.26",
            shlex.join(["docker", "exec", "-i", "tilexr-combine-ht-f71d0252-v11", "python3", "-"])]
    result = subprocess.run(argv, input=REMOTE.encode(), capture_output=True, timeout=90)
    assert result.returncode == 0, "model_profile_query_failed"
    output = json.loads(result.stdout)
    directory = Path("D:/project/仿真配置")
    output["documented_login_note_exists"] = any((directory / name).is_file() for name in ("仿真系统环境信息.md", "仿真系统环境信息"))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
