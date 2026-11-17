"""One bounded SSH read of two already accepted statistics files."""
from pathlib import Path
import datetime, hashlib, json, subprocess, time

work=Path(__file__).resolve().parent
source=(work/'details_remote.py').read_bytes()
contract=(work/'details-contract.json').read_bytes()
# The contract is JSON data; embedding its Python repr cannot execute its contents.
loader=('import sys\n'
        'sys.argv=["details_remote.py",'+repr(contract.decode())+']\n'
        'exec(compile('+repr(source.decode())+',"details_remote.py","exec"))\n').encode()
key=Path.home()/'.ssh/id_ed25519';assert key.is_file()
argv=['ssh','-i',str(key),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1','root@141.61.73.107','python3 -']
started=datetime.datetime.now(datetime.timezone.utc).isoformat();clock=time.monotonic()
try:
    p=subprocess.run(argv,input=loader,capture_output=True,timeout=120)
    out,err,code,expired=p.stdout,p.stderr,p.returncode,False
except subprocess.TimeoutExpired as error:
    out,err,code,expired=error.stdout or b'',error.stderr or b'',None,True
(work/'targetedp2-details.stdout.log').write_bytes(out)
(work/'targetedp2-details.stderr.log').write_bytes(err)
(work/'targetedp2-details.receipt.json').write_text(json.dumps(dict(started_utc=started,finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),seconds=round(time.monotonic()-clock,3),exit_code=code,timed_out=expired,remote_script_sha256=hashlib.sha256(loader).hexdigest(),stdout_bytes=len(out),stderr_bytes=len(err)))+'\n')
assert code==0 and not expired,'Read-only details failed; inspect saved output'
result=json.loads(out);assert result['status']=='PASS' and result['npu_launch'] is False and result['device_queries'] is False
text=json.dumps(result,ensure_ascii=True,separators=(',',':'));assert len(text.encode())<14000
print(text)
