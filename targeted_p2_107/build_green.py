"""Upload immutable build inputs and run one isolated build, without device kernels."""
from pathlib import Path
import datetime,hashlib,json,subprocess,time
ROOT='/root/tilelang-107-targeted-p2-20261117-run01'
PAYLOAD=json.loads(Path(__file__).with_name('transport.json').read_text())['payload_sha256']

def main():
    work=Path(__file__).resolve().parent
    assert hashlib.sha256((work/'payload.zip').read_bytes()).hexdigest()==PAYLOAD
    key=Path.home()/'.ssh/id_ed25519';assert key.is_file()
    opts=['-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
    target='root@141.61.73.107'
    def command(label,argv,timeout,data=None):
        started=datetime.datetime.now(datetime.timezone.utc).isoformat();clock=time.monotonic()
        try:
            p=subprocess.run(argv,input=data,capture_output=True,timeout=timeout)
            out,err,code,expired=p.stdout,p.stderr,p.returncode,False
        except subprocess.TimeoutExpired as e:
            out,err,code,expired=e.stdout or b'',e.stderr or b'',None,True
        (work/(label+'.stdout.log')).write_bytes(out)
        (work/(label+'.stderr.log')).write_bytes(err)
        (work/(label+'.receipt.json')).write_text(json.dumps({'started_utc':started,'finished_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'seconds':round(time.monotonic()-clock,3),'exit_code':code,'timed_out':expired,'stdout_bytes':len(out),'stderr_bytes':len(err)})+'\n')
        if expired or code!=0:raise RuntimeError(label+' failed: audit saved state before retry')
        return out
    prepare=('from pathlib import Path\nimport os,hashlib,json\n'
        'assert os.geteuid()==0\n'
        "assert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\n"
        'p=Path('+repr(ROOT)+');p.mkdir(exist_ok=False)\n'
        "print(json.dumps({'created':True,'npu_launch':False}))\n").encode()
    command('targetedp2-create',['ssh',*opts,target,'python3 -'],45,prepare)
    for name in ('payload.zip','operations.py','build_remote.py','experiment.json','transport.json'):
        command('targetedp2-scp-'+name.replace('.','-'),['scp',*opts,str(work/name),target+':'+ROOT+'/'+name],120)
    out=command('targetedp2-build',['ssh',*opts,target,'python3 '+ROOT+'/build_remote.py'],2400)
    result=json.loads(out.decode('utf-8'))
    assert result['status']=='PASS' and result['npu_launch'] is False
    result.pop('compiled_artifacts',None)
    text=json.dumps(result,ensure_ascii=True,separators=(',',':'))
    assert len(text.encode())<14000
    print(text)
if __name__=='__main__':main()
