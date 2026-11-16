"""Green entry: transfer verified retained dependencies and pinned P4 inputs; no NPU."""
from pathlib import Path
import hashlib,json,os,subprocess,time,zipfile
RES=Path(__file__).resolve().parent
WORK=Path.cwd()
EXP='/root/tilelang-107-multirank-e28d87de-20261116-run01'
OPTS=['-i',str(Path.home()/'.ssh/id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
ENV=os.environ.copy();ENV.update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(label,argv,limit,data=None):
 start=time.monotonic()
 try:p=subprocess.run([str(a) for a in argv],input=data,capture_output=True,timeout=limit,env=ENV)
 except subprocess.TimeoutExpired as e:
  (WORK/(label+'.stdout.log')).write_bytes(e.stdout or b'');(WORK/(label+'.stderr.log')).write_bytes(e.stderr or b'');(WORK/(label+'.receipt.json')).write_text(json.dumps({'timed_out':True}));raise RuntimeError('Timed out: inspect saved effects without replay') from None
 (WORK/(label+'.stdout.log')).write_bytes(p.stdout);(WORK/(label+'.stderr.log')).write_bytes(p.stderr);(WORK/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode,'seconds':round(time.monotonic()-start,3)}))
 assert p.returncode==0,'Phase failed: inspect saved effects without replay: '+label
 return p.stdout
inputs=RES/'build-inputs.zip';assert sha(inputs)=='70712c887fc736d110a171d06ac19bb34454a802ea7b025f6d760aeca1f1e5bf'
raw=run('dependency-pack-106',['ssh',*OPTS,'root@141.61.73.106','/usr/bin/python3 -'],120,(RES/'pack_retained.py').read_bytes())
archive=WORK/'dependencies.zip';archive.write_bytes(raw)
with zipfile.ZipFile(archive) as z:assert z.testzip() is None;manifest=json.loads(z.read('dependency-manifest.json'))
assert manifest['revision']=='e28d87deb1e141e23ace5900594218ff47ab2525'
create="from pathlib import Path\nimport hashlib,json,os,shutil\nassert hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\np=Path("+repr(EXP)+")\nassert os.geteuid()==0 and shutil.disk_usage(p.parent).free>2147483648\np.mkdir(mode=0o750,exist_ok=False)\n"
run('create-107',['ssh',*OPTS,'root@141.61.73.107','/usr/bin/python3 -'],25,create.encode())
run('transfer-dependencies-107',['scp',*OPTS,archive,'root@141.61.73.107:'+EXP+'/dependencies.zip'],100)
run('transfer-inputs-107',['scp',*OPTS,inputs,'root@141.61.73.107:'+EXP+'/build-inputs.zip'],60)
transfer={'dependency_zip_sha256':sha(archive),'input_zip_sha256':sha(inputs),'dependency_manifest_files':len(manifest['files']),'source_host':'141.61.73.106','target':'141.61.73.107','npu_launch':False}
(WORK/'transfer.json').write_text(json.dumps(transfer,indent=2)+'\n')
run('transfer-receipt-107',['scp',*OPTS,WORK/'transfer.json','root@141.61.73.107:'+EXP+'/transfer.json'],20)
result=json.loads(run('prepare-and-core-build-107',['ssh',*OPTS,'root@141.61.73.107','/usr/bin/python3 -'],450,(RES/'prepare_build.py').read_bytes()))
(WORK/'source-and-core-build-summary.json').write_text(json.dumps(result,indent=2)+'\n')
assert result['status']=='PASS' and result['npu_launch'] is False
print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
