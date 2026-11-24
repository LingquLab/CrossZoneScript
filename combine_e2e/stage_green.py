# Copyright (c) 2026 TileXR Project.
"""Deliver immutable stage fixtures and execute one foreground green SST phase."""
import argparse,base64,json,os,subprocess
from pathlib import Path

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--args-file',type=Path,required=True)
    args=json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args)=={'world','tokens','phase'}
    assert args['world'] in (16,32,64) and args['tokens'] in (4,16) and args['phase'] in ('send','reduce','fused','serial')
    directory=Path(__file__).parent
    asset=directory/'data'/('%dp-t%d'%(args['world'],args['tokens']))
    request=dict(args,identity=json.loads((asset/'identity.json').read_text()),
                 archive=base64.b64encode((asset/'fixture.tar.gz').read_bytes()).decode(),
                 checker=(directory/'check_local.py').read_text(encoding='utf-8'))
    code=(directory/'local_host.py').read_text(encoding='utf-8')
    if args['phase']=='serial':
        code+='\nrequest='+repr(request)+'\nfor phase in ("send","reduce","fused"):\n request["phase"]=phase\n code=main(request)\n if code: raise SystemExit(code)\n'
    else:
        code+='\nraise SystemExit(main('+repr(request)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    logs.mkdir(parents=True,exist_ok=True)
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    argv=['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
          '-o','ConnectTimeout=12','root@141.61.94.26','timeout --kill-after=20 3450 python3 -']
    proc=subprocess.run(argv,input=code.encode(),capture_output=True,timeout=3490)
    stem='%dp-t%d-local-%s'%(args['world'],args['tokens'],args['phase'])
    (logs/(stem+'.stdout')).write_bytes(proc.stdout)
    (logs/(stem+'.stderr')).write_bytes(proc.stderr)
    if proc.stdout.strip():
        records=[json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        result={'phases':records} if args['phase']=='serial' else records[0]
        assert len(proc.stdout)<25000
        print(json.dumps(result,separators=(',',':')))
    else: print(json.dumps({'ssh_exit':proc.returncode,'evidence':'green_local_logs','stage':stem}))
    return proc.returncode

if __name__=='__main__': raise SystemExit(main())
