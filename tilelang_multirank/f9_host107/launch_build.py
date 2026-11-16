from pathlib import Path
import hashlib,json,os,subprocess,time

work=Path(__file__).resolve().parent
repo=work/'f9-host107-build-script-repo';repo.mkdir(exist_ok=False)
commit='12a39c3ca39dfd7a0d404d37ff07aa53b5cbabb4'
files={
 'tilelang_multirank/f9_host107/ab.zip':('601fb8381b25614673b51d92e1a983cd4816a119','5a4011c99da6ffcca4c5ce743778f090aae4f4d3a2345f4b4c6191b653984ba9'),
 'tilelang_multirank/f9_host107/build_ab.py':('39a2f0f7bc1483593b6cf374e7fcdc644a680554','4b2b353f67b605ce8dbac97ddc4d3c7e29b349870297952094d97dac8d9c2ac2')}
env=os.environ.copy();env.update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='never')
def run(label,argv,limit,cwd=None,data=None):
 started=time.monotonic()
 try:p=subprocess.run(argv,cwd=cwd,env=env,input=data,capture_output=True,timeout=limit)
 except subprocess.TimeoutExpired as e:
  (work/(label+'.stdout.log')).write_bytes(e.stdout or b'');(work/(label+'.stderr.log')).write_bytes(e.stderr or b'')
  (work/(label+'.receipt.json')).write_text(json.dumps({'timed_out':True}))
  raise RuntimeError('Timeout: inspect saved state without replay') from None
 (work/(label+'.stdout.log')).write_bytes(p.stdout);(work/(label+'.stderr.log')).write_bytes(p.stderr)
 (work/(label+'.receipt.json')).write_text(json.dumps({'exit_code':p.returncode,'seconds':round(time.monotonic()-started,3)}))
 return p
def git(label,args,limit=25):
 p=run(label,['git',*args],limit,cwd=repo)
 assert p.returncode==0,'Git preparation failed: '+label
 return p.stdout
git('git-init',['init','--quiet'])
git('git-fetch',['-c','http.https://github.com/.sslVerify=false','fetch','--no-tags','https://github.com/LingquLab/CrossZoneScript.git','refs/heads/993bfd9c-fe38-4cb2-ad72-fc0bd92a9a5e'],120)
git('git-reachability',['merge-base','--is-ancestor',commit,'FETCH_HEAD'])
resources={}
for index,(entry,(oid,expected)) in enumerate(files.items()):
 row=git('git-entry-'+str(index),['ls-tree','-r',commit,'--',entry]).decode().strip()
 assert row=='100644 blob '+oid+chr(9)+entry
 raw=git('git-blob-'+str(index),['cat-file','blob',oid]);assert hashlib.sha256(raw).hexdigest()==expected
 resources[Path(entry).name]=raw
(work/'f9-host107-build-source.json').write_text(json.dumps({'commit':commit,'files':files,'npu_launch':False},indent=2)+chr(10))
opts=['-i',str(Path.home()/'.ssh/id_ed25519'),'-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=15','-o','ConnectionAttempts=1']
exp='/root/tilelang-107-f9-host107-b520293d-20261116-run01'
create="from pathlib import Path\nimport hashlib,os,shutil\nassert os.geteuid()==0 and hashlib.sha256(Path('/etc/machine-id').read_bytes()).hexdigest()=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'\np=Path("+repr(exp)+")\nassert shutil.disk_usage(p.parent).free>3*1024**3\np.mkdir(mode=0o750,exist_ok=False)\n"
p=run('ssh-create',['ssh',*opts,'root@141.61.73.107','/usr/bin/python3 -'],25,data=create.encode())
assert p.returncode==0,'Create failed: inspect state without replay'
bundle=work/'f9-host107-ab.zip';bundle.write_bytes(resources['ab.zip'])
p=run('scp-ab',['scp',*opts,str(bundle),'root@141.61.73.107:'+exp+'/ab.zip'],80)
assert p.returncode==0,'Payload transfer failed: inspect state without replay'
p=run('ssh-build-ab',['ssh',*opts,'root@141.61.73.107','/usr/bin/python3 -'],1000,data=resources['build_ab.py'])
result={}
if p.stdout.strip().startswith(b'{'):
 result=json.loads(p.stdout);(work/'107-f9-host107-build-summary.json').write_text(json.dumps(result,indent=2)+chr(10))
assert p.returncode==0,'Remote A/B build failed: inspect saved state without replay'
assert result['task_id']=='tl-a5-107-f9-host107-build-ab-20261116-01' and result['revision']=='b520293d5f014fcbd4897a334d50ebf9ca9c601b'
assert result['status']=='PASS' and result['npu_launch'] is False and result['official_hccl_launch'] is False
assert result['runtime_validation']=='NOT_RUN' and result['clock']=='NOT_VERIFIED'
assert result['qualification_reused'] and result['qualification_receipt_sha256']=='e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
assert result['cores']==[64]*4 and result['core_cxx11_abi']==0
assert result['source_files_per_variant']==123 and result['exports_per_variant']==16 and result['fixtures_verified']==8
assert result['host_binaries']==result['linked_device_elfs']==result['relocatable_device_objects']==64
assert result['original_configuration_preserved'] and result['bundle_sha256']==files['tilelang_multirank/f9_host107/ab.zip'][1]
assert all(p['exit_code']==0 and not p['timed_out'] for p in result['phases'])
compact=json.dumps(result,ensure_ascii=True,separators=(',',':'));assert len(compact.encode())<16000
print(compact)
