# Copyright (c) 2026 TileXR Project.
"""Compare concrete Host ABI fields with the simulator's serialized contracts."""

import json
from pathlib import Path
import shutil
import subprocess
import sys

EXPERIMENT = Path('/work/tooling/tools/unitest/simulator/experiments/alltoall_udma')
sys.path.insert(0, str(EXPERIMENT))
import experiment
import runtime

folder = Path('/work/resume-7de1/abi-contract-a1')
folder.mkdir(exist_ok=False)


def differences(actual, expected, path=''):
    if isinstance(actual, dict) and isinstance(expected, dict):
        rows = []
        for key in sorted(set(actual) | set(expected)):
            rows += differences(actual.get(key), expected.get(key), path+'/'+key)
        return rows
    if actual != expected:
        return [dict(field=path, actual=actual, expected=expected)]
    return []


probe = Path('/work/resume-7de1/alltoall-control-a1/build/abi_probe')
proc = subprocess.run([str(probe), '8', '1'], capture_output=True, text=True, timeout=15)
assert proc.returncode == 0, 'control_probe_failed'
actual = json.loads(proc.stdout)
abi = experiment.kernel_abi()
expected = dict(abi_size=abi['size'], fields={field['name']:dict(offset=field['param_offset'],size=field['size']) for field in abi['fields']},
    layout=experiment.layout(8,1), block_dim=experiment.BLOCK_DIM, qp_count=experiment.QP_COUNT, runtime_abi=runtime.runtime_abi())
result = dict(ok=True, diagnostic_only=True, simulator_run=False, alltoall_differences=differences(actual,expected)[:30])
sizes = {'comm_bytes':'CommArgs','info_bytes':'UDMAInfo','sq_bytes':'UDMAWQCtx','cq_bytes':'UDMACQCtx','mem_bytes':'UDMAMemInfo',
    'profile_bytes':'TileXRUDMAProfileRegistry','sqe_bytes':'UDMASqeCtx','sge_bytes':'UDMASgeCtx','cqe_bytes':'UDMACqeCtx',
    'registry_bytes':'TileXRUDMARegistry','region_bytes':'TileXRUDMARegionDesc'}
offsets = {'info_sq':('UDMAInfo','sqPtr'),'info_cq':('UDMAInfo','scqPtr'),'info_mem':('UDMAInfo','memPtr'),
    'sq_buffer':('UDMAWQCtx','bufAddr'),'sq_head':('UDMAWQCtx','headAddr'),'sq_tail':('UDMAWQCtx','tailAddr'),
    'sq_doorbell':('UDMAWQCtx','dbAddr'),'sq_token':('UDMAWQCtx','localTokenId'),'sq_count':('UDMAWQCtx','wqeCntAddr'),
    'cq_buffer':('UDMACQCtx','bufAddr'),'cq_tail':('UDMACQCtx','tailAddr'),'cq_doorbell':('UDMACQCtx','dbAddr'),
    'mem_tpn':('UDMAMemInfo','tpn'),'mem_tid':('UDMAMemInfo','tid'),'mem_length':('UDMAMemInfo','len'),
    'mem_address':('UDMAMemInfo','addr'),'mem_eid':('UDMAMemInfo','eidAddr'),'registry_regions':('TileXRUDMARegistry','regions')}
source = ['// Copyright (c) 2026 TileXR Project.',
    '#include <cstddef>','#include <cstdint>','#include <iostream>',
    '#include <tilexr/comm/udma/types.h>','#include <tilexr/comm/udma/registry.h>',
    'int main() { std::cout << "{";']
items = [(name,'sizeof(TileXR::'+cpp+')') for name,cpp in sizes.items()]
items += [(name,'offsetof(TileXR::'+cpp+','+member+')') for name,(cpp,member) in offsets.items()]
for i,(name,expression) in enumerate(items):
    label = (',' if i else '')+'"'+name+'":'
    source.append('std::cout << '+json.dumps(label)+' << '+expression+';')
source.append('std::cout << "}\\n"; }')
path = folder / 'candidate_abi.cpp'
path.write_text('\n'.join(source)+'\n')
compiler = shutil.which('g++')
assert compiler, 'host_compiler_missing'
proc = subprocess.run([compiler,'-std=c++17','-I/work/candidate/include',str(path),'-o',str(folder/'candidate_abi')],capture_output=True,text=True,timeout=45)
result['candidate_compile_exit'] = proc.returncode
if proc.returncode == 0:
    proc = subprocess.run([str(folder/'candidate_abi')],capture_output=True,text=True,timeout=15)
    assert proc.returncode == 0
    candidate = json.loads(proc.stdout)
    result['candidate_abi'] = candidate
    shared = {k:v for k,v in runtime.runtime_abi().items() if k in candidate}
    result['candidate_runtime_differences'] = differences({k:candidate[k] for k in shared}, shared)
    result['candidate_registry_matches'] = candidate['registry_bytes']==16+256*16 and candidate['registry_regions']==16 and candidate['region_bytes']==16
else:
    result['compile_errors'] = [line[:350] for line in proc.stderr.splitlines() if 'error:' in line][:3]
(folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=True))
