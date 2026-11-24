# Copyright (c) 2026 TileXR Project.
"""Run one bounded task phase through the green Windows SSH identity."""
import argparse,hashlib,json,os,subprocess
from pathlib import Path
SESSION='fac4ae28-1586-42fe-b55b-ff09982f17ec'
def main():
    p=argparse.ArgumentParser();p.add_argument('--args-file',required=True,type=Path)
    args=json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))
    assert args['action'] in ('prepare','smoke','qualify-local','local','return','instrument','repair-profile-send','pipeline-build','pipeline','status','stop')
    directory=Path(__file__).parent
    names=('fixture.py','prefill.cpp','exports.py','check_local.py','profile.py','instrument.py','profile_flush.h','return_fixture.py',
           'check_return.py','pipeline_kernel.cpp','pipeline_fixture.py','check_pipeline.py','pipeline_build.sh')
    files={name:(directory/name).read_text(encoding='utf-8') for name in names}
    request=dict(args,files=files)
    code=(directory/'host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(request)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr'/SESSION;logs.mkdir(parents=True,exist_ok=True)
    stem=args['action']+'-'+str(args.get('tokens',4))+'-'+str(args.get('phase',args.get('stage','')))
    seconds={'prepare':600,'smoke':1700,'qualify-local':2400,'local':1100,'instrument':700,'repair-profile-send':1800,'return':1250,'pipeline-build':400,'pipeline':1600,'status':60,'stop':90}[args['action']]
    cmd=['ssh','-T','-i',str(Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'),'-p','22','-o','BatchMode=yes',
         '-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12',
         'root@141.61.94.26','timeout --kill-after=20 '+str(seconds)+' python3 -']
    proc=subprocess.run(cmd,input=code.encode(),capture_output=True,timeout=seconds+40)
    (logs/(stem+'.stdout')).write_bytes(proc.stdout);(logs/(stem+'.stderr')).write_bytes(proc.stderr)
    if proc.stdout.strip():
        records=[json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        assert len(proc.stdout)<45000
        print(json.dumps({'records':records,'ssh_exit':proc.returncode},separators=(',',':')))
    else:print(json.dumps({'ssh_exit':proc.returncode,'stage':stem,'evidence':str(logs)}))
    return proc.returncode
if __name__=='__main__':raise SystemExit(main())
