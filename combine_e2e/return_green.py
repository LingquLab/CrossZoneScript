# Copyright (c) 2026 TileXR Project.
"""Execute exactly one return-path SST stage through the approved SSH channel."""
import argparse,json,os,subprocess
from pathlib import Path
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--args-file',type=Path,required=True)
    args=json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))
    assert set(args) in ({'stage'},{'stage','tokens'}) and args['stage'] in ('return','inter-send','return-independent','inter-send-independent','final-independent','final-joint','independent-transport','complete-4')
    tokens=args.get('tokens',16)
    assert tokens in (4,16)
    directory=Path(__file__).parent
    files={name:(directory/name).read_text(encoding='utf-8') for name in ('return_fixture.py','check_return.py','exports.py')}
    files['send-abis.json']=(directory/('data/64p-t%d/send-abis.json'%tokens)).read_text(encoding='utf-8')
    request={'stage':args['stage'],'tokens':tokens,'files':files}
    code=(directory/'return_host.py').read_text(encoding='utf-8')
    if args['stage']=='complete-4':
        assert tokens==4
        code+='\nrequest='+repr(request)+'\nrequest["stage"]="return"\nreturn_ok=main(request)==0\nrequest["stage"]="inter-send" if return_ok else "inter-send-independent"\ninter_ok=main(request)==0\nrequest["stage"]="final-independent"\nfinal_ok=main(request)==0\njoint_ok=False\nif return_ok and inter_ok:\n request["stage"]="final-joint"\n joint_ok=main(request)==0\nraise SystemExit(0 if return_ok and inter_ok and final_ok and joint_ok else 1)\n'
    elif args['stage']=='independent-transport':
        code+='\nrequest='+repr(request)+'\nfailed=False\nfor stage in ("return-independent","inter-send-independent"):\n request["stage"]=stage\n failed=bool(main(request)) or failed\nraise SystemExit(1 if failed else 0)\n'
    else:code+='\nraise SystemExit(main('+repr(request)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    proc=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                         '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                         'timeout --kill-after=20 6200 python3 -'],input=code.encode(),capture_output=True,timeout=6240)
    stem='64p-t%d-%s'%(tokens,args['stage'])
    (logs/(stem+'.stdout')).write_bytes(proc.stdout)
    (logs/(stem+'.stderr')).write_bytes(proc.stderr)
    if proc.stdout.strip():
        records=[json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
        print(json.dumps({'stages':records} if args['stage'] in ('independent-transport','complete-4') else records[0],separators=(',',':')))
    else:print(json.dumps({'stage':args['stage'],'ssh_exit':proc.returncode,'evidence':'green_local_logs'}))
    return proc.returncode
if __name__=='__main__':raise SystemExit(main())
