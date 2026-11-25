"""Return compact unified-run status and a bounded inventory of trace fields."""
import json
from pathlib import Path
import subprocess
import sys

here=Path(__file__).resolve().parent
sys.path[:0]=[str(here.parent/'ht_t32_e2e'),str(here.parent/'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST
from config import CASE, T32_CASE, T32_LONG_CASE

options=json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2]==['--args-file'] else {}
tokens = int(options.get('tokens', 4))
assert tokens in (4, 32)
if tokens == 32:
    CASE = T32_LONG_CASE if options.get('long_wall') else T32_CASE
    assert not options.get('dl') and not options.get('overlap')
source=f'case_name={CASE!r}\n'+r'''
from collections import Counter
import json
from pathlib import Path
import re

root=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
case=root/case_name
report=dict(exists=case.is_dir())
for filename in ('run.json','phase-summary.json','unified-result.json'):
    path=case/filename
    if path.is_file():
        data=json.loads(path.read_text())
        report[filename]={key:data[key] for key in ('status','returncode','runner_exit_code','timeout_seconds','sim_us','guards')
                          if key in data}
path=case/'sim.log'
if path.is_file():
    with path.open('rb') as f:
        first=f.read(2*1024*1024)
    report['log']=dict(bytes=path.stat().st_size,cqe_in_prefix=first.count(b'WrCQEResp'))
    if case_name.startswith('ht-parallel-ep64-t32-'):
        all_log = path.read_bytes()
        report['log']['cqe'] = all_log.count(b'WrCQEResp')
        stamps = [int(match.group(1)) for line in all_log.splitlines() if b'WrCQEResp' in line
                  if (match := re.search(rb'\bSim:\s*(\d+)', line))]
        report['log']['last_cqe_stamp'] = max(stamps, default=None)
    report['log']['hits']={key:bool(re.search(pattern,first,re.I))
                               for key,pattern in (('unsupported',rb'(?:unsupported|not support)'),
                               ('fatal',rb'\bfatal\b'),('kernel',rb'parallel_kernel'))}
paths=list(case.glob('OUTPUT/**/*trace*'))
kinds=Counter(re.sub(r'\d+','N',p.name) for p in paths if p.is_file())
report['trace_inventory']=kinds.most_common(16)
ba=case/'OUTPUT/stat_trace/Host0_ba_layer_trace.json'
if ba.is_file():
    with ba.open('rb') as f:
        raw=f.read(1024*1024)
    matches=list(re.finditer(rb'\{[^{}]*"busPayloadType_"[^{}]*\}',raw))
    fields=set()
    actions=Counter()
    for m in matches[:400]:
        try: value=json.loads(m.group())
        except ValueError: continue
        fields.update(value)
        actions[value['busPayloadType_']]+=1
    report['ba_schema']=dict(keys=sorted(fields),actions=actions,bytes=ba.stat().st_size,
                             ts_fields=len(re.findall(rb'"ts"\s*:',raw)))
if case_name.startswith('ht-parallel-ep64-t32-'):
    saved = case/'unified-result.json'
    if saved.is_file():
        result = json.loads(saved.read_text())
        report['result'] = {key:result[key] for key in ('status','returncode','runner_exit_code','sim_us','kernel_sha256',
                                              'guards','checked','checker_detail','cqe') if key in result}
        report.pop('ba_schema', None)
        report.pop('trace_inventory', None)
        checker = case/'check.log'
        report['checker_log'] = dict(exists=checker.is_file())
        if checker.is_file():
            report['checker_log']['bytes'] = checker.stat().st_size
            for line in checker.read_text(errors='replace').splitlines():
                if line.startswith('{"ranks":'):
                    report['checker_log']['summary'] = json.loads(line)
                    break
print(json.dumps(report,separators=(',',':')))
'''
if options.get('compare_inputs'):
    assert tokens == 32
    source = (here/'config.py').read_text() + r'''
import json
from pathlib import Path
root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
first, second = (root/name for name in (T32_CASE, T32_LONG_CASE))
a, b = (json.loads((case/'case-manifest.json').read_text()) for case in (first, second))
assert a['kernel_sha256'] == b['kernel_sha256'] == KERNEL_SHA256
left, right = ({name: digest for name, digest in manifest['files'].items() if name.startswith('input_bin/')}
               for manifest in (a,b))
left_golden, right_golden = ({name: digest for name, digest in manifest['fixture_files'].items()
                             if name != 'manifest.json'} for manifest in (a,b))
assert left == right and left_golden == right_golden
wa, wb = ((case/'workload.yml').read_text().replace(case.name, T32_CASE) for case in (first,second))
assert json.loads(wa) == json.loads(wb)
print(json.dumps(dict(input_files=len(left), golden_and_layout_files=len(left_golden),
                      normalized_workloads_equal=True, kernel_sha256=KERNEL_SHA256),separators=(',',':')))
'''
elif options.get('queues'):
    assert tokens == 32
    function = 'queue_trace'
    if options.get('detail'):
        function = 'detail_cross_queues' if options.get('cross') else 'detail_gather_queues'
    if options.get('count_cq'):
        function = 'count_cq_slots'
    source = (here/'config.py').read_text() + (here/'queue_trace.py').read_text() + f'''
import json
from pathlib import Path
case = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native') / {CASE!r}
print(json.dumps({function}(case),separators=(',',':')))
'''
elif options.get('dl'):
    source=(here/'overlap.py').read_text()+(here/'dl_scan.py').read_text()+f'''
from pathlib import Path
case=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native') / {CASE!r}
print(json.dumps(dl_coverage(case),separators=(',',':')))
'''
elif options.get('overlap'):
    source=(here/'overlap.py').read_text()+f'''
from pathlib import Path
case=Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native') / {CASE!r}
print(json.dumps(summarize_saved_overlap(case),separators=(',',':')))
'''
compile(source, '<unified-status>', 'exec')
ssh=['ssh','-T','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10','-o',
     'BatchMode=no','-o','NumberOfPasswordPrompts=1','-o','PreferredAuthentications=password',
     '-o','PubkeyAuthentication=no',HOST,'python3','-']
with askpass_environment() as env:
    proc=subprocess.run(ssh,input=source.encode(),env=env,capture_output=True,timeout=85)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode,stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout),separators=(',',':')))
