"""Report bounded diagnostic evidence from the retained final-reduce reuse failure."""
import json
from pathlib import Path
import re
import subprocess

SESSION = '1a5bdfed-7c8c-47ca-b7b6-827c1777651a'
ROOT = Path('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-c030900-host18-a1'
CANN = '/opt/sim/Ascend/cann-9.1.0'
REMOTE = r"""
from pathlib import Path
import json
cann=Path('/opt/sim/Ascend/cann-9.1.0')
roots=(cann/'aarch64-linux/ascendc/include',cann/'aarch64-linux/asc/include',cann/'compiler/tikcpp')
libs=[]
for root in roots:
    libraries=sorted(root.rglob('*tpipe*')) if root.is_dir() else []
    for path in libraries:
        if path.suffix not in ('.h','.hpp') or not path.is_file():
            continue
        lines=path.read_text(errors='replace').splitlines()
        methods={}
        for index,line in enumerate(lines):
            for name in ('TPipe::TPipe', 'TPipe::~TPipe', 'TPipe::Reset', 'TPipe::Destroy', 'GetTPipePtr', 'SetTPipePtr'):
                if name in line and name not in methods:
                    methods[name]=[text.strip()[:140] for text in lines[index:index+5]]
        if methods:
            libs.append({'file':path.name,'methods':methods})
print(json.dumps({'tpipe':libs[:6]}))
"""


def main(request):
    assert request['host'] == 'host-94-18' and request['bs'] == 4
    identity = json.loads((ROOT/'identity.json').read_text())
    cid = identity['container_id']
    info = json.loads(subprocess.check_output(['docker','inspect',cid],timeout=12))[0]
    assert info['Config']['Labels'].get('crosszone.session') == SESSION
    assert info['Image'] == identity['image']
    case = ROOT/'cases/t4-joint-reuse-final-a6'
    row = json.loads((case/'phase-result.json').read_text())
    assert row['status'] == 'failed' and row['returncode'] == 255
    log = case/'sim.log'
    with log.open('rb') as file:
        file.seek(max(0, file.seek(0,2) - 262144))
        lines = file.read().decode(errors='replace').splitlines()
    focal = [line[:250] for line in lines if re.search(r'David_V100_52_aicore5_aiv1|pc=|pc:|instr.*error',line,re.I)]
    traces = case/'OUTPUT/stat_trace'
    sample_names = [path.name for path in sorted(traces.glob('*52*')) if path.is_file()][:14] if traces.is_dir() else []
    report = {'fatal_window':focal[-7:], 'trace_names_rank52':sample_names,
                      'last_run_phase':row.get('stage'), 'candidate_commit':row['candidate_commit'],
                      'model_sha':row['model_config']['sha256']}
    checked = subprocess.run(['docker','exec',cid,'python3','-c',REMOTE],capture_output=True,timeout=35)
    assert checked.returncode == 0
    report.update(json.loads(checked.stdout))
    print(json.dumps(report,separators=(',',':')))
    return 0
