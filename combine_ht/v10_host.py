"""Run the user-authorized V1.0 comparison with exact candidate and route identity."""
import json,shutil,subprocess,sys,time
from pathlib import Path
A=json.loads(sys.argv[1])
SESSION='f71d0252-ef1e-4b5c-b299-c2429ebcfbdf'
BASE=Path('/var/tmp/tilexr-combine-ht-'+SESSION+'-v11')
WORK=Path('/var/tmp/tilexr-combine-ht-'+SESSION+'-v10')
NAME='tilexr-combine-ht-f71d0252-v10'
IMAGE='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
REVISIONS={'candidate':'29f973b0d322105957ccaf31e2d4539f6fe00a24','tooling':'23a542b2e6bfcc34f85313d02677a4aafdfc3efb'}
def call(argv,timeout=60):
    proc=subprocess.run(list(map(str,argv)),capture_output=True,timeout=timeout)
    with (WORK/'host.log').open('ab') as log:log.write(proc.stdout+proc.stderr)
    assert proc.returncode==0,'command_exit_'+str(proc.returncode)
    return proc.stdout.decode().strip()
def invoke(args,timeout):
    return json.loads(call(['docker','exec',NAME,'python3','/work/sst.py',json.dumps(args)],timeout))
def compact(value):
    return {k:v for k,v in value.items() if k not in ['model','perf_resources','model_libraries']}
result={'ok':False,'image_id':IMAGE,'revisions':REVISIONS,'host_work':str(WORK),'cases':[]}
try:
    assert json.loads((BASE/'revisions.json').read_text())==REVISIONS,'candidate_identity'
    WORK.mkdir(exist_ok=False)
    assert call(['docker','image','inspect','--format','{{.Id}} {{.Os}}/{{.Architecture}}',IMAGE])==IMAGE+' linux/arm64'
    for name in ['candidate','tooling']:
        shutil.copytree(BASE/name,WORK/name)
    for name in ['sst.py','fixtures.py','profile_dump.py','prefill_bridge.cpp']:
        shutil.copyfile(BASE/name,WORK/name)
    s=(WORK/'sst.py').read_text()
    s=s.replace('elif len(candidates) == 1 and (key == "installed_model.py" or directory == "UBusSim_deployment"):','elif len(candidates) == 1:')
    s=s.replace('bf93fd6d0749fc3c6eaae365c7cba558988e1fa25c1b9a26e139be61b60a3da3','6d4cd3b729177646ca1c43d57971e5266a946405b6f820f3d0d914114b7ccfce')
    s=s.replace('rank_size=ranks, log_level=1, instruction_dump_chips=[]','rank_size=ranks, log_level=0, instruction_dump_chips=[]')
    s=s.replace('    for name, config in configs.items():', '    def quiet(value):\n        if isinstance(value, dict):\n            for key, child in value.items():\n                if key in ("enable_dump", "enable_dump_detail"): value[key] = 0\n                elif key == "stat_trace_flag": value[key] = False\n                else: quiet(child)\n        elif isinstance(value, list):\n            for child in value: quiet(child)\n    quiet(configs)\n    for name, config in configs.items():')
    (WORK/'sst.py').write_text(s)
    (WORK/'revisions.json').write_text(json.dumps(REVISIONS))
    call(['docker','run','-dt','--name',NAME,'--label','crosszone.session='+SESSION,
          '--network=none','--runtime=ascend','--ipc=shareable','--shm-size=64m',
          '--ulimit','nofile=524288:524288','--mount','type=bind,src='+str(WORK)+',dst=/work',IMAGE,'/bin/bash','-l'],90)
    info=json.loads(call(['docker','inspect','--format','{{json .}}',NAME]))
    result['container_id']=info['Id']
    assert info['Image']==IMAGE and info['Config']['Labels']['crosszone.session']==SESSION
    build=invoke({'stage':'setup'},420)
    result['build']=compact(build)
    assert build['ok'],'candidate_build_failed'
    kernel=build['kernel_sha256']
    for mode in ['send','reduce']:
        args=dict(stage='run',ranks=8,tokens=16,hidden=512,mode=mode,case_id='8p-bs16-29f973b0-v10-a1',threads=32,stage_timeout=A['stage_timeout'],kernel_sha256=kernel)
        phase=invoke(args,A['stage_timeout']+95)
        result['cases'].append(compact(phase))
        (WORK/'run-summary.json').write_text(json.dumps(result,indent=2))
        if not phase['ok']:break
    result['ok']=len(result['cases'])==2 and all(v['ok'] for v in result['cases'])
except (OSError,ValueError,AssertionError,subprocess.SubprocessError) as error:
    result['error_type']=type(error).__name__;result['detail']=str(error)[:350]
finally:
    if WORK.exists():(WORK/'run-summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
raise SystemExit(0 if result['ok'] else 1)
