# Copyright (c) 2026 TileXR Project.
"""Run the pinned GM-access diagnostic in the existing idle simulation container."""
import argparse,json,os,subprocess
from pathlib import Path

HOST=r'''
import json,subprocess
from pathlib import Path
root=Path('/mnt/share/tilexr-sim-jobs/ea219fe0-2177-4850-84f0-c0da68a4a5fa/run-013cc3e-a1')
identity=json.loads((root/'identity.json').read_text())
cid=identity['container_id']
assert cid=='fd0becad7ba477492b44c5343221c32864bd3a8bd2f2b723706e6e405a5e5714'
tools=root/('queue-access-cache-tools-a4' if fresh else 'queue-access-probe-tools-a1');tools.mkdir(exist_ok=False)
for name,content in request.items():
 assert name in ('queue_access_probe.cpp','queue_access_probe_run.py')
 (tools/name).write_text(content)
with (root/('queue-access-cache-a4-container.log' if fresh else 'queue-access-probe-container.log')).open('wb') as log:
 p=subprocess.run(['docker','exec',cid,'bash','-c','set -e; source /opt/sim/cann-env.sh; export PATH=/opt/sim/Ascend/cann-9.1.0/tools/bisheng_compiler/bin:/opt/sim/Ascend/cann-9.1.0/aarch64-linux/ccec_compiler/bin:$PATH; exec python3 "$1" "$2" "$3"','bash','/work/'+tools.name+'/queue_access_probe_run.py','/work/'+tools.name,'fresh' if fresh else 'native'],stdout=log,stderr=subprocess.STDOUT,timeout=330)
report=root/('queue-access-probe-cache-a4' if fresh else 'queue-access-probe-a1')/'result.json'
result=json.loads(report.read_text()) if report.is_file() else {'evidence':'queue-access-probe-container.log','report_exists':False}
result.pop('all_rows',None)
print(json.dumps(dict(result,container_command_exit=p.returncode),separators=(',',':')))
raise SystemExit(p.returncode)
'''
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--args-file',type=Path,required=True)
    action=json.loads(parser.parse_args().args_file.read_text(encoding='utf-8'))
    assert action in ({'action':'probe'},{'action':'probe_fresh'})
    fresh=action['action']=='probe_fresh'
    directory=Path(__file__).parent
    request={name:(directory/name).read_text(encoding='utf-8') for name in ('queue_access_probe.cpp','queue_access_probe_run.py')}
    code='request='+repr(request)+'\nfresh='+repr(fresh)+'\n'+HOST
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    logs=Path(os.environ['LOCALAPPDATA'])/'CrossZone/tilexr/ea219fe0-2177-4850-84f0-c0da68a4a5fa'
    p=subprocess.run(['ssh','-T','-i',str(key),'-p','22','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12','root@141.61.94.26','timeout --kill-after=10 360 python3 -'],input=code.encode(),capture_output=True,timeout=390)
    prefix='queue-access-cache-a4' if fresh else 'queue-access-probe'
    (logs/(prefix+'.stdout')).write_bytes(p.stdout);(logs/(prefix+'.stderr')).write_bytes(p.stderr)
    if p.stdout.strip():print(json.dumps(json.loads(p.stdout),separators=(',',':')))
    else:print(json.dumps({'ssh_exit':p.returncode,'evidence':'green_local_logs'}))
    return p.returncode
if __name__=='__main__':raise SystemExit(main())
