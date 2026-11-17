"""Transfer a pinned CPU extractor and return only its compact saved-evidence summary."""
from pathlib import Path
import ast
import datetime
import hashlib
import json
import subprocess
import time

ROOT = '/root/tilelang-107-fixedp2-083ce35e-20261117-run01'


def main():
    work = Path(__file__).resolve().parent
    remote = (work / 'export_remote.py').read_bytes()
    constants = {}
    for node in ast.parse(remote).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ('BUILD_SHA', 'PARENT_SHA'):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert constants['PARENT_SHA'] != 'PENDING_CAPTURE_RECEIPT'
    hashes = {name: hashlib.sha256((work / name).read_bytes()).hexdigest()
              for name in ('export_remote.py', 'statistics_cpu.py')}
    key = Path.home() / '.ssh/id_ed25519'; assert key.is_file()
    options = ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1']
    target = 'root@141.61.73.107'

    def command(label, argv, timeout, data=None):
        start = datetime.datetime.now(datetime.timezone.utc).isoformat(); clock = time.monotonic()
        try:
            p = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
            out, err, code, expired = p.stdout, p.stderr, p.returncode, False
        except subprocess.TimeoutExpired as error:
            out, err, code, expired = error.stdout or b'', error.stderr or b'', None, True
        (work / (label + '.stdout.log')).write_bytes(out)
        (work / (label + '.stderr.log')).write_bytes(err)
        (work / (label + '.receipt.json')).write_text(json.dumps(dict(
            started_utc=start, finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            seconds=round(time.monotonic()-clock,3), exit_code=code, timed_out=expired,
            stdout_bytes=len(out), stderr_bytes=len(err),
            remote_script_sha256=hashlib.sha256(data).hexdigest() if data else None))+'\n')
        if code != 0 or expired:
            raise RuntimeError(label + ' failed; audit saved output, never replay device capture')
        return out

    prepare = ('from pathlib import Path\nimport hashlib,json,os\n'
        'r=Path('+repr(ROOT)+')\n'
        'assert os.geteuid()==0\n'
        "assert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\n"
        'assert hashlib.sha256((r/"build-summary.json").read_bytes()).hexdigest()=='+repr(constants['BUILD_SHA'])+'\n'
        'assert hashlib.sha256((r/"capture-launch/summary.json").read_bytes()).hexdigest()=='+repr(constants['PARENT_SHA'])+'\n'
        '(r/"export-support01").mkdir(exist_ok=False)\n'
        'print(json.dumps({"status":"PREPARED","npu_launch":False,"device_queries":False}))\n').encode()
    result = command('fixedp2-export-prepare', ['ssh', *options, target, 'python3 -'], 60, prepare)
    assert json.loads(result)['status'] == 'PREPARED'
    for name in hashes:
        command('fixedp2-export-scp-'+name.replace('.','-'), ['scp', *options, str(work/name),
                target+':'+ROOT+'/export-support01/'+name], 90)
    loader = ('from pathlib import Path\nimport hashlib,sys\n'
        'root=Path('+repr(ROOT) + ')/"export-support01"\n'
        'expected='+repr(hashes)+'\n'
        'for name,digest in expected.items():\n'
        ' assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest\n'
        'sys.path.insert(0,str(root))\n'
        'p=root/"export_remote.py"\n'
        'exec(compile(p.read_bytes(),str(p),"exec"),{"__file__":str(p),"__name__":"__main__"})\n').encode()
    out = command('fixedp2-export', ['ssh', *options, target, 'python3 -'], 900, loader)
    result = json.loads(out.decode('utf-8'))
    assert result['status']=='PASS' and result['rank_captures_reanalyzed']==20
    assert result['npu_launch'] is False and result['device_queries'] is False
    text=json.dumps(result,ensure_ascii=True,separators=(',',':'))
    assert len(text.encode())<15000
    print(text)


if __name__=='__main__':main()
