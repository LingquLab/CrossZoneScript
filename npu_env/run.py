import argparse
import base64
import json
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
args = json.loads(Path(parser.parse_args().args_file).read_text(encoding='utf-8'))
assert args['host'] == 'root@141.61.94.18'
root = Path(__file__).parent
files = {name: base64.b64encode((root / name).read_bytes()).decode() for name in ('check_env.sh', 'npu_info.sh', 'inspect_cann_state.py')}
remote = '''import base64,json,pathlib,subprocess,tempfile
files=json.loads(PAYLOAD)
with tempfile.TemporaryDirectory(prefix="npu-env-") as directory:
    root=pathlib.Path(directory)
    for name,data in files.items():
        (root/name).write_bytes(base64.b64decode(data))
    stages=[("identity",["sh","-c","whoami; uname -smr; cat /etc/os-release"]),("driver",["sh","-c",'for f in /usr/local/Ascend/driver/version.info /usr/local/Ascend/firmware/version.info; do if [ -f "$f" ]; then printf "%s\\n" "$f"; head -c 4096 "$f"; fi; done']),("environment",["bash","check_env.sh"]),("npu",["bash","npu_info.sh"])]
    failed=False
    for name,command in stages:
        try:
            r=subprocess.run(command,cwd=directory,capture_output=True,text=True,timeout=60)
            print(json.dumps({"stage":name,"exit_code":r.returncode,"stdout":r.stdout[:24000],"stderr":r.stderr[:2000]},ensure_ascii=False),flush=True)
            failed=failed or r.returncode!=0
        except subprocess.TimeoutExpired:
            print(json.dumps({"stage":name,"status":"TIMEOUT"}),flush=True)
            failed=True
    raise SystemExit(1 if failed else 0)
'''.replace('PAYLOAD', repr(json.dumps(files)))
command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2', args['host'], 'python3 -']
try:
    result = subprocess.run(command,input=remote,text=True,encoding='utf-8',errors='replace',capture_output=True,timeout=210)
    evidence={'host':args['host'],'ssh_exit':result.returncode,'stdout':result.stdout,'stderr':result.stderr}
    Path('npu-env-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(evidence,ensure_ascii=False))
    raise SystemExit(result.returncode)
except subprocess.TimeoutExpired:
    print(json.dumps({'host':args['host'],'status':'SSH_TIMEOUT'}))
    raise SystemExit(124)
