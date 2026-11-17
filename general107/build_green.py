"""Bounded SHA-checked transfer and build only; no NPU kernels/collectives."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

PAYLOAD_SHA='87832d7036715e973a53fd4a71f22debd56c4332269ee5ef2ba424c26b8e7416'
TARGET='root@141.61.73.107'
ROOT='/root/tilelang-107-general-64ed5828-20261117-run01'

def main():
    work=Path(__file__).resolve().parent
    payload=work/'payload.zip'
    assert payload.stat().st_size==10210703 and hashlib.sha256(payload.read_bytes()).hexdigest()==PAYLOAD_SHA
    key=Path.home()/'.ssh/id_ed25519';assert key.is_file()
    options=['-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
    def command(label,argv,timeout,data=None):
        p=subprocess.run(argv,input=data,capture_output=True,timeout=timeout)
        (work/(label+'.stdout.log')).write_bytes(p.stdout)
        (work/(label+'.stderr.log')).write_bytes(p.stderr)
        (work/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode,'stdout_bytes':len(p.stdout),'stderr_bytes':len(p.stderr)})+'\n')
        if p.returncode:raise RuntimeError(label+' failed; inspect saved receipts before retry')
        return p.stdout
    prepare='''from pathlib import Path
import hashlib,json,os
assert os.geteuid()==0
assert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
p=Path('/root/tilelang-107-general-64ed5828-20261117-run01')
assert not p.exists(),'Saved experiment exists; audit without replay'
p.mkdir(mode=0o750)
(p/'transfer-started.json').write_text(json.dumps({'candidate':'64ed582898e1321a0e93a2c4e0e78a6deb2f4868','npu_launch':False})+'\\n')
print(json.dumps({'status':'PREPARED','npu_launch':False}))
'''
    command('general-create',['ssh',*options,TARGET,'python3 -'],45,prepare.encode())
    command('general-scp',['scp',*options,str(payload),TARGET+':'+ROOT+'/payload.zip'],180)
    build='''from pathlib import Path,PurePosixPath
import hashlib,json,os,stat,subprocess,sys,zipfile
root=Path('/root/tilelang-107-general-64ed5828-20261117-run01')
p=root/'payload.zip'
assert hashlib.sha256(p.read_bytes()).hexdigest()=='87832d7036715e973a53fd4a71f22debd56c4332269ee5ef2ba424c26b8e7416'
assert set(x.name for x in root.iterdir())=={'transfer-started.json','payload.zip'}
with zipfile.ZipFile(p) as z:
    assert z.testzip() is None and len(z.namelist())==len(set(z.namelist()))
    manifest=json.loads(z.read('payload-manifest.json'))
    assert set(z.namelist())==set(manifest['files'])|{'payload-manifest.json'}
    for i in z.infolist():
        name=PurePosixPath(i.filename)
        assert not name.is_absolute() and '..' not in name.parts and not stat.S_ISLNK(i.external_attr>>16)
    for name,digest in manifest['files'].items():
        assert hashlib.sha256(z.read(name)).hexdigest()==digest
    z.extractall(root)
proc=subprocess.run(['/usr/bin/python3',str(root/'build_remote.py')],cwd=root,capture_output=True,timeout=850)
(root/'build-parent.stdout.log').write_bytes(proc.stdout)
(root/'build-parent.stderr.log').write_bytes(proc.stderr)
(root/'build-parent.receipt.json').write_text(json.dumps({'exit_code':proc.returncode})+'\\n')
if proc.returncode:
    print(json.dumps({'status':'FAIL','npu_launch':False,'saved_summary':str(root/'build-summary.json')}))
    sys.exit(proc.returncode)
summary=json.loads((root/'build-summary.json').read_text())
assert summary['status']=='PASS' and summary['npu_launch'] is False
print(json.dumps(summary,separators=(',',':')))
'''
    stdout=command('general-build-ssh',['ssh',*options,TARGET,'python3 -'],900,build.encode())
    result=json.loads(stdout.decode('utf-8'))
    assert result['status']=='PASS' and result['npu_launch'] is False
    text=json.dumps(result,ensure_ascii=True,separators=(',',':'))
    assert len(text.encode())<16000
    print(text)

if __name__=='__main__':
    main()
