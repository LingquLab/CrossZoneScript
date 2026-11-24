"""Validate a new pinned candidate in the existing, idle V1.0 task container."""
import json,shutil,subprocess,sys
from pathlib import Path
A=json.loads(sys.argv[1])
SESSION='f71d0252-ef1e-4b5c-b299-c2429ebcfbdf'
BASE=Path('/var/tmp/tilexr-combine-ht-'+SESSION+'-v11')
WORK=Path('/var/tmp/tilexr-combine-ht-'+SESSION+'-v10')
NAME='tilexr-combine-ht-f71d0252-v10'
IMAGE='sha256:19e063d4eebab9785ad24b175ee1567dfb24c1180698f67fe0ab32a731a19cd5'
claimed=False
result={'ok':False,'cases':[],'host_work':str(WORK),'image_id':IMAGE}
def call(argv,timeout=60):
    p=subprocess.run(list(map(str,argv)),capture_output=True,timeout=timeout)
    with (WORK/'continue-host.log').open('ab') as log:log.write(p.stdout+p.stderr)
    assert p.returncode==0,'command_exit_'+str(p.returncode)
    return p.stdout.decode().strip()
def invoke(args,timeout):
    return json.loads(call(['docker','exec',NAME,'python3','/work/sst.py',json.dumps(args)],timeout))
def compact(value):return {k:v for k,v in value.items() if k not in ['model','perf_resources','model_libraries']}
try:
    info=json.loads(call(['docker','inspect','--format','{{json .}}',NAME]))
    assert info['Image']==IMAGE and info['Config']['Labels']['crosszone.session']==SESSION
    assert any(m['Source']==str(WORK) and m['Destination']=='/work' for m in info['Mounts'])
    rows=call(['docker','top',NAME,'-eo','pid,comm,stat']).splitlines()[1:]
    assert not any(row.split()[1]=='sstsim.x' and not row.split()[2].startswith('Z') for row in rows),'existing_sst_active'
    revisions=json.loads((BASE/'revisions.json').read_text())
    assert revisions['candidate']==A['revision'],'staged_candidate_identity'
    before=json.loads((WORK/'revisions.json').read_text())
    assert before['candidate']!=A['revision'],'candidate_upgrade_already_attempted'
    claimed=True
    for name in ['candidate','tilexr_moonep_combine_ht_kernel.o','tilexr_moonep_combine_ht_kernel_rel.o','build.json','build.log','prefill.so','revisions.json','run-summary.json']:
        p=WORK/name
        if p.exists():p.rename(WORK/('previous-'+before['candidate']+'-'+name))
    shutil.copytree(BASE/'candidate',WORK/'candidate')
    (WORK/'revisions.json').write_text(json.dumps(revisions))
    result['revisions']=revisions
    build=invoke({'stage':'setup'},420)
    result['build']=compact(build)
    assert build['ok'],'candidate_build_failed'
    for mode in ['send','reduce']:
        args=dict(stage='run',ranks=8,tokens=16,hidden=512,mode=mode,case_id=A['case_id'],threads=32,stage_timeout=A['stage_timeout'],kernel_sha256=build['kernel_sha256'])
        phase=invoke(args,A['stage_timeout']+95)
        result['cases'].append(compact(phase))
        (WORK/'run-summary.json').write_text(json.dumps(result,indent=2))
        if not phase['ok']:break
    result['ok']=len(result['cases'])==2 and all(v['ok'] for v in result['cases'])
except (OSError,ValueError,AssertionError,subprocess.SubprocessError) as error:
    result['error_type']=type(error).__name__;result['detail']=str(error)[:350]
finally:
    if claimed:(WORK/'run-summary.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
raise SystemExit(0 if result['ok'] else 1)
