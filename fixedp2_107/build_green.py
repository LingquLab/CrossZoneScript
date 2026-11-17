"""Upload immutable build inputs and run one isolated build, without device kernels."""
from pathlib import Path
import hashlib,json,subprocess
ROOT='/root/tilelang-107-fixedp2-083ce35e-20261117-run01'
PAYLOAD='1e051e143d954eb38a45e3b7e4f072a9252ea688da65e36b7869716112ed43ea'

def main():
    work=Path(__file__).resolve().parent
    assert hashlib.sha256((work/'payload.zip').read_bytes()).hexdigest()==PAYLOAD
    key=Path.home()/'.ssh/id_ed25519';assert key.is_file()
    opts=['-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
    target='root@141.61.73.107'
    def command(label,argv,timeout,data=None):
        p=subprocess.run(argv,input=data,capture_output=True,timeout=timeout)
        (work/(label+'.stdout.log')).write_bytes(p.stdout)
        (work/(label+'.stderr.log')).write_bytes(p.stderr)
        (work/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode,'stdout_bytes':len(p.stdout),'stderr_bytes':len(p.stderr)})+'\n')
        if p.returncode:raise RuntimeError(label+' failed: audit saved state before retry')
        return p.stdout
    prepare=('from pathlib import Path\nimport os,hashlib,json\n'
        'assert os.geteuid()==0\n'
        "assert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\n"
        'p=Path('+repr(ROOT)+');p.mkdir(exist_ok=False)\n'
        "print(json.dumps({'created':True,'npu_launch':False}))\n").encode()
    command('fixedp2-create',['ssh',*opts,target,'python3 -'],45,prepare)
    for name in ('payload.zip','operations.py','build_remote.py'):
        command('fixedp2-scp-'+name.replace('.','-'),['scp',*opts,str(work/name),target+':'+ROOT+'/'+name],120)
    out=command('fixedp2-build',['ssh',*opts,target,'python3 '+ROOT+'/build_remote.py'],800)
    result=json.loads(out.decode('utf-8'))
    assert result['status']=='PASS' and result['npu_launch'] is False
    text=json.dumps(result,ensure_ascii=True,separators=(',',':'))
    assert len(text.encode())<14000
    print(text)
if __name__=='__main__':main()
