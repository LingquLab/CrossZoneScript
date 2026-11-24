# Copyright (c) 2026 TileXR Project.
"""Invoke the prepared complete HT SST entry through the approved green host."""
import argparse,json,os,subprocess
from pathlib import Path
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--args-file',type=Path,required=True)
    assert json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))=={}
    directory=Path(__file__).parent
    files={name:(directory/name).read_text(encoding='utf-8') for name in ('pipeline_fixture.py','prefill.cpp','check_pipeline.py','exports.py')}
    files['send-abis.json']=(directory/'data/64p-t4/send-abis.json').read_text(encoding='utf-8')
    code=(directory/'pipeline_host.py').read_text(encoding='utf-8')+'\nraise SystemExit(main('+repr(files)+'))\n'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
                      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26',
                      'timeout --kill-after=20 1700 python3 -'],input=code.encode(),capture_output=True,timeout=1740)
    (logs/'pipeline-run.stdout').write_bytes(p.stdout);(logs/'pipeline-run.stderr').write_bytes(p.stderr)
    if p.stdout.strip():print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:print(json.dumps({'ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode
if __name__=='__main__':raise SystemExit(main())
