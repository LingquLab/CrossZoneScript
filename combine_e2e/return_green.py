# Copyright (c) 2026 TileXR Project.
"""Execute exactly one return-path SST stage through the approved SSH channel."""
import argparse,json,os,subprocess
from pathlib import Path
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--args-file',type=Path,required=True)
    args=json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args)=={'stage'} and args['stage'] in ('return','inter-send','final-independent','final-joint')
    directory=Path(__file__).parent
    files={name:(directory/name).read_text(encoding='utf-8') for name in ('return_fixture.py','check_return.py','exports.py')}
    files['send-abis.json']=(directory/'data/64p-t16/send-abis.json').read_text(encoding='utf-8')
    request={'stage':args['stage'],'files':files}
    code=(directory/'return_host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(request)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    proc=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                         '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                         'timeout --kill-after=20 1600 python3 -'],input=code.encode(),capture_output=True,timeout=1640)
    stem='64p-t16-'+args['stage']
    (logs/(stem+'.stdout')).write_bytes(proc.stdout)
    (logs/(stem+'.stderr')).write_bytes(proc.stderr)
    if proc.stdout.strip():print(json.dumps(json.loads(proc.stdout),separators=(',',':')))
    else:print(json.dumps({'stage':args['stage'],'ssh_exit':proc.returncode,'evidence':'green_local_logs'}))
    return proc.returncode
if __name__=='__main__':raise SystemExit(main())
