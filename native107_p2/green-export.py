"""[AI] Stage and execute only CPU projection, then export bounded numeric evidence."""
from pathlib import Path
import base64,hashlib,json,os,sqlite3,subprocess,time
TASK='xr-native107-p2-export-20261116-01'
EXP='/root/native-tlxr-p2-107-5c25e0f0-20261116-run01';DEST=EXP+'/numeric-export-script-01'
FILES={'export-numeric.py': ('5ef6ecfbbab049c149687d66441082ffe19d7b9d', 'fc83270f9d47b56946b487d00dd4d543e182b872579b30c1f938c6506f112eff'), 'export-identity.json': ('8e9034c427ce3e38da810ce2a5cdff57fa3746a9', 'aae12c42bf4cac8993441b253f6932bd29ce4a1c7fdb0bbad190067674e5a699')}
root=Path.cwd();work=root/'xr107-p2-export-20261116-01';work.mkdir(exist_ok=False);repo=Path(SCRIPT_REPO)
r={'task_id':TASK,'status':'FAIL_OR_UNVERIFIED','device_execution':'NOT_RUN','replay':False,'commit':PINNED_COMMIT}
def run(label,args,timeout,data=None,cwd=None):
 try:p=subprocess.run(args,input=data,capture_output=True,timeout=timeout,cwd=cwd)
 except subprocess.TimeoutExpired as e:
  (work/(label+'.out')).write_bytes(e.stdout or b'');(work/(label+'.err')).write_bytes(e.stderr or b'')
  raise RuntimeError('CPU_OUTPUTS_UNKNOWN_READ_SAVED_STATE_NO_REPLAY') from None
 (work/(label+'.out')).write_bytes(p.stdout);(work/(label+'.err')).write_bytes(p.stderr)
 (work/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode}))
 assert p.returncode==0,'STAGE_FAILED_'+label
 return p.stdout
stage='saved-capture-gate'
try:
 con=sqlite3.connect((root/'bridge.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=5);con.execute('PRAGMA query_only=ON');active=con.execute("SELECT task_id FROM tasks WHERE state IN ('RUNNING','QUEUED','WAITING','WAITING_ANSWER') AND task_id!=? LIMIT 20",(TASK,)).fetchall();con.close();assert not active,'OTHER_ACTIVE_TASK'
 saved=json.loads((root/'xr107-p2-capture-20261116-02/capture-transport-summary.json').read_text());assert saved['status']==saved['remote']['status']=='PASS' and saved['remote']['capture_groups_passed']==10
 stage='fixed-blob-sha'
 assert run('head',['git','rev-parse','FETCH_HEAD'],15,cwd=repo).decode().strip()==PINNED_COMMIT
 for n,(blob,digest) in FILES.items():
  assert run('blob-id-'+n,['git','rev-parse',PINNED_COMMIT+':native107_p2/'+n],15,cwd=repo).decode().strip()==blob
  raw=run('blob-'+n,['git','cat-file','blob',blob],20,cwd=repo);assert hashlib.sha256(raw).hexdigest()==digest,'SCRIPT_SHA'
  if n.endswith('.py'):compile(raw,n,'exec')
  (work/n).write_bytes(raw)
 opts=['-i',str(Path.home()/'.ssh/id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
 stage='fresh-cpu-export-stage'
 prepare="from pathlib import Path\nimport os,hashlib\nassert os.geteuid()==0 and hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\np=Path("+repr(DEST)+");assert not p.exists(),'SAVED_STAGE_READ_ONLY_RECOVERY';p.mkdir(mode=0o750)\n"
 run('prepare',['ssh',*opts,'root@141.61.73.107','python3 -'],40,prepare.encode())
 run('copy-script',['scp',*opts,*[str(work/n) for n in FILES],'root@141.61.73.107:'+DEST+'/'],60)
 stage='cpu-extract-archive'
 bootstrap="from pathlib import Path\nimport hashlib,os\np=Path("+repr(DEST)+")\ne="+repr({n:v[1] for n,v in FILES.items()})+"\nassert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==h for n,h in e.items()),'EXPORT_SCRIPT_SHA'\nos.execv('/usr/bin/python3',['/usr/bin/python3',str(p/'export-numeric.py')])\n"
 delivery=json.loads(run('extract',['ssh',*opts,'root@141.61.73.107','python3 -'],800,bootstrap.encode()))
 assert delivery['status']=='PASS' and delivery['source_groups']==10 and delivery['work_rows']==1280 and delivery['device_execution']=='NOT_RUN'
 stage='compact-numeric-transfer'
 remote=delivery['numeric_zip'];run('download-numeric',['scp',*opts,'root@141.61.73.107:'+remote['path'],str(work/'numeric.zip')],75)
 raw=(work/'numeric.zip').read_bytes();assert len(raw)==remote['bytes'] and hashlib.sha256(raw).hexdigest()==remote['sha256'],'NUMERIC_ZIP_TRANSFER_SHA'
 text=base64.b64encode(raw).decode('ascii');(work/'numeric.zip.b64').write_text(text)
 chunk=text[:16000]
 r.update(status='PASS',delivery=delivery,numeric_transfer={'filename':'numeric.zip','base64_chars':len(text),'offset':0,'length':len(chunk),'chunk_sha256':hashlib.sha256(chunk.encode()).hexdigest(),'zip_sha256':remote['sha256'],'base64':chunk})
except BaseException as e:
 r.update(blocked_stage=stage,error_type=type(e).__name__)
 if isinstance(e,(AssertionError,RuntimeError)):r['reason_code']=str(e)[:150]
text=json.dumps(r,separators=(',',':'));assert len(text.encode())<28000
(work/'export-transport-summary.json').write_text(text+'\n');print(text)
