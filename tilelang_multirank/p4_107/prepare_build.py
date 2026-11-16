"""Verify staged sources and build only the matching ABI0 core on 107."""
from pathlib import Path,PurePosixPath
import concurrent.futures,hashlib,json,os,shlex,signal,stat,subprocess,time,zipfile
EXP=Path('/root/tilelang-107-multirank-e28d87de-20261116-run01')
CANN=Path('/usr/local/Ascend/cann-9.1.0')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,value):p.write_text(json.dumps(value,indent=2)+'\n')
assert sha(Path('/etc/machine-id'))=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0'
assert sha(Path('/etc/hccl_rootinfo.json'))=='56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523'
receipt=Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
assert sha(receipt)=='e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
q=json.loads(receipt.read_text());assert q['status']=='PASS' and q['identity']['devices']==[0,1,2,3]
assert not (EXP/'sources-ready.json').exists() and not (EXP/'TileXR').exists(),'Prepared/build state exists: inspect without replay'
transfer=json.loads((EXP/'transfer.json').read_text())
assert sha(EXP/'dependencies.zip')==transfer['dependency_zip_sha256']
assert sha(EXP/'build-inputs.zip')=='70712c887fc736d110a171d06ac19bb34454a802ea7b025f6d760aeca1f1e5bf'
def unpack(path,manifest_name):
 with zipfile.ZipFile(path) as z:
  assert z.testzip() is None
  infos=z.infolist();assert len(infos)<2500 and sum(i.file_size for i in infos)<50*1024*1024
  for i in infos:
   p=PurePosixPath(i.filename);assert not p.is_absolute() and '..' not in p.parts and not stat.S_ISLNK(i.external_attr>>16)
  assert len({i.filename for i in infos})==len(infos)
  m=json.loads(z.read(manifest_name));assert set(z.namelist())==set(m['files'])|{manifest_name}
  for name,digest in m['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest,name
  for i in infos:
   dst=EXP/i.filename;dst.parent.mkdir(parents=True,exist_ok=True)
   if dst.exists():assert sha(dst)==hashlib.sha256(z.read(i.filename)).hexdigest(),i.filename
   else:dst.write_bytes(z.read(i.filename))
 return m
base=unpack(EXP/'dependencies.zip','dependency-manifest.json')
payload=unpack(EXP/'build-inputs.zip','p4-payload-manifest.json')
assert base['revision']==payload['revision']=='e28d87deb1e141e23ace5900594218ff47ab2525'
prep=json.loads((EXP/'preparation.json').read_text())
assert prep['cores']==[64]*4 and prep['candidate_files']==base['candidate_files']
assert len(prep['candidate_files'])==123 and len(prep['abi_rows'])==16 and len(prep['cases'])==8
for name,digest in prep['candidate_files'].items():assert sha(EXP/'TileXR'/name)==digest
for name,digest in prep['files'].items():assert sha(EXP/name)==digest
for tokens in (4,16,64,256):
 for rank in range(4):
  gen=EXP/'generated'/('t'+str(tokens))/('rank'+str(rank));meta=json.loads((gen/'export.json').read_text());adapt=json.loads((gen/'adaptation.json').read_text())
  assert (meta['world_size'],meta['experts'],meta['rank'],meta['cores'],meta['tokens_per_rank'])==(4,8,rank,64,tokens)
  assert adapt['abi']=={'version':2,'args_size':104} and adapt['args_bytes_aarch64']==104
  assert meta['backend_revision']=='557b11b8b132c6d6ecd8d5b56b74785783c9cc1a' and meta['backend_diff_sha256']=='05f56abcc9e90f5fad157e46e839d672bfec798c437c888f471cd055a6d56956'
  assert adapt['modules'][0]['ub_bytes']==meta['receive_ub_plan']['planned_bytes']<=194560
  for name,digest in adapt['hashes'].items():assert sha(gen/name)==digest
source=EXP/'TileXR';backend=EXP/'backend';core=EXP/'core-install'
assert sha(backend/'src/tl_templates/ascend/common.h')=='d6355337873e5f7368c3ec7cb1f22ef0494663e133152120fee20265f03a1780'
save(EXP/'sources-ready.json',{'revision':base['revision'],'source_file_count':len(base['files']),'payload_file_count':len(payload['files']),'core_build':'NOT_RUN','npu_launch':False})
phases=[]
def run(label,argv,limit):
 save(EXP/(label+'.command.json'),[str(a) for a in argv]);started=time.monotonic();timed=False
 command='source '+shlex.quote(str(CANN/'set_env.sh'))+' >/dev/null 2>&1 && export ASCEND_HOME_PATH='+shlex.quote(str(CANN))+' && exec '+shlex.join([str(a) for a in argv])
 with (EXP/(label+'.log')).open('xb') as out:
  p=subprocess.Popen(['bash','-c',command],cwd=source,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
  save(EXP/(label+'.started.json'),{'pid':p.pid,'start_ticks':Path('/proc/'+str(p.pid)+'/stat').read_text().split(') ',1)[1].split()[19]})
  try:code=p.wait(timeout=limit)
  except subprocess.TimeoutExpired:
   timed=True;os.killpg(p.pid,signal.SIGTERM)
   try:code=p.wait(timeout=5)
   except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);code=p.wait(timeout=5)
 row={'phase':label,'exit_code':code,'timed_out':timed,'seconds':round(time.monotonic()-started,3)};phases.append(row);save(EXP/(label+'.receipt.json'),row)
 return code==0 and not timed
args=['cmake','-S',source,'-B',EXP/'core-build','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_PREFIX='+str(core),'-DCMAKE_INSTALL_LIBDIR=lib64','-DBUILD_TESTING=OFF','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_EP=OFF','-DTILEXR_BUILD_CHECKER=OFF']
passed=run('core-configure',args,60)
if passed:passed=run('core-build',['cmake','--build',EXP/'core-build','-j4'],300)
if passed:passed=run('core-install',['cmake','--install',EXP/'core-build'],40)
result={'schema':'tilelang_107_p4_source_and_core_build.v1','target':'141.61.73.107','revision':base['revision'],'base_revision':base['base_revision'],'status':'PASS' if passed else 'CORE_BUILD_FAILED','experiment_dir':str(EXP),'source_hash_verification':'PASS','candidate_files_verified':123,'exports_verified':16,'fixtures_verified':8,'cores':[64]*4,'core_cxx11_abi':0,'phases':phases,'npu_launch':False,'tilelang_device_compile':'NOT_RUN','qualification_environment_key':q['environment_key']}
if passed:
 lib=core/'lib64/libtile-comm.so';assert lib.is_file();result['core_library_sha256']=sha(lib)
 check=subprocess.run(['readelf','-d',str(lib)],capture_output=True,timeout=15);assert check.returncode==0 and b'devlib' not in check.stdout
 result['runtime_devlib_rpath']=False
else:
 lines=(EXP/(phases[-1]['phase']+'.log')).read_text(errors='replace').splitlines();result['diagnostics']=[s[:180] for s in lines if any(t in s.lower() for t in ['error:','cmake error','undefined reference','fatal'])][:5]
for name,digest in prep['candidate_files'].items():assert sha(source/name)==digest
save(EXP/'source-and-core-build-summary.json',result)
print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
raise SystemExit(0 if passed else 1)
