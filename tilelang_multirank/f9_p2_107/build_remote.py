"""COORD008 P2-only bounded independent ABI0/CANN build; no device kernel launches."""
from pathlib import Path, PurePosixPath
import hashlib, json, os, shlex, shutil, signal, stat, subprocess, time, zipfile

TASK = 'tl-a5-107-f9-p2-build-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
EXP = Path('/root/tilelang-107-f9-p2-b520293d-20261116-run01')
OLD = Path('/root/tilelang-107-multirank-e28d87de-20261116-run01')
CANN = Path('/usr/local/Ascend/cann-9.1.0')
BUNDLE_SHA = '51f8c1655d5583a3edca3f24dd3fba44aeeb53166d7dc1218c12b628d4d398c5'
QUAL = Path('/root/tilelang-hccl-qualifications/693ec683dcc80807f1ccc0c0356a6e032008fd71e1a9a1538c435eaa1a153dc6/supplements/layer0-peer-private-view-single/receipt.json')
QUAL_SHA = 'e22807e4b530ded459533c840eb9e77722204776f5e99f2994d5a0640fe1b609'
HOST = {'runner.cpp':'a725d0d2645e9b9a1c56c9da6bb3f1af2d61f585e157e14bd564cf9d3f221c33','pre_allreduce.h':'0d610dc7029cc89beff7cc37e9ae5b1fd280cb99469cdac3e1800eccb3a5eacf','native_host_barrier.h':'2f1cd0d65311503809adec7d78e34a49caec9b8090ad4c9387ecafdbdeeac146'}
SYSTEM = {'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523','/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rawsha(raw): return hashlib.sha256(raw).hexdigest()
def save(p, value):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix+'.tmp'); tmp.write_text(json.dumps(value,indent=2)+'\n'); tmp.replace(p)
def require(ok, detail):
    if not ok: raise RuntimeError(detail)

phases = []
deadline = time.monotonic()+900
result = {'schema':'tilelang_f9_p2_107_build.v1','task_id':TASK,'revision':REV,'target':'141.61.73.107','authorization':'COORD-20261116-008','devices':[0,1],'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'official_hccl_launch':False,'device_runtime_probe':False,'runtime_validation':'NOT_RUN','clock':'NOT_VERIFIED','experiment_dir':str(EXP),'phases':phases}
def run(label, argv, limit, cann=True):
    limit = min(limit, int(deadline-time.monotonic()))
    require(limit>0,'Build phase budget exhausted; inspect saved state')
    argv = [str(a) for a in argv]
    save(EXP/(label+'.command.json'), argv)
    cmd = shlex.join(argv)
    if cann: cmd = 'source '+shlex.quote(str(CANN/'set_env.sh'))+' >/dev/null 2>&1 && export ASCEND_HOME_PATH='+shlex.quote(str(CANN))+' && exec '+cmd
    started = time.monotonic(); timed = False
    with (EXP/(label+'.log')).open('xb') as out:
        p = subprocess.Popen(['bash','-c',cmd],cwd=EXP,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
        identity = {'pid':p.pid}
        try: identity['start_ticks'] = Path('/proc/'+str(p.pid)+'/stat').read_text().split(') ',1)[1].split()[19]
        except FileNotFoundError: identity['already_exited'] = True
        save(EXP/(label+'.started.json'),identity)
        try: code = p.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed = True
            try: os.killpg(p.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: code = p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(p.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                code = p.wait(timeout=5)
    row = {'phase':label,'exit_code':code,'timed_out':timed,'seconds':round(time.monotonic()-started,3)}
    phases.append(row); save(EXP/(label+'.receipt.json'),row)
    require(code == 0 and not timed,'First failing phase: '+label)

def archive(path, manifest_name, wrapped, limit):
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        require(len(infos)<2500 and sum(i.file_size for i in infos)<limit,'Archive size gate')
        require(len({i.filename for i in infos}) == len(infos) and z.testzip() is None,'Archive duplicate/CRC gate')
        for i in infos:
            p = PurePosixPath(i.filename)
            require(not p.is_absolute() and '..' not in p.parts and not stat.S_ISLNK(i.external_attr>>16),'Unsafe archive member')
        m = json.loads(z.read(manifest_name)); files = m['files'] if wrapped else m
        require(set(z.namelist()) == set(files)|{manifest_name},'Archive manifest membership')
        data = {}
        for name, expected in files.items():
            raw = z.read(name); require(rawsha(raw)==expected,'Archive SHA mismatch: '+name); data[name] = raw
    return m,data

started_build = False
try:
    require(os.geteuid()==0 and sha('/etc/machine-id')=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0','Host identity gate')
    require(EXP.is_dir() and set(p.name for p in EXP.iterdir())=={'preflight','p2.zip','build_remote.py','build_idle_gate.py'},'Prepared/build state exists: inspect without replay')
    started_build = True
    require(shutil.disk_usage(EXP).free>3*1024**3,'Free disk gate')
    require(not os.environ.get('LD_PRELOAD') and not os.environ.get('ASCEND_VISIBLE_DEVICES'),'Unexpected process overrides')
    for name, expected in SYSTEM.items(): require(sha(name)==expected,'System configuration changed')
    require(sha(QUAL)==QUAL_SHA,'Qualification receipt changed')
    qual = json.loads(QUAL.read_text())
    require(qual['status']=='PASS' and qual['identity']['devices']==[0,1,2,3] and qual['identity']['cann']=='9.1.0','Matching qualification gate')
    require(qual['environment_key']=='e4db0e0177a56f1daa97c54c6e7a296067a74315b33a7c06b96a65d0a4b67836','Qualification environment gate')
    require(not qual['own_residual_children'] and qual['original_configuration_preserved'],'Qualification cleanup gate')
    require(sha(CANN/'include/hccl/hccl_comm.h')=='8f6d9baa988a4673266601d83f0a5d112bbed2764ef34ba1efaa67279c099255','Installed header changed')
    require(sha(CANN/'tools/hccl_test/common/src/hccl_test_common.cc')=='b4114ac3975f08cb80a8d27e0e41acfca89f425e86846df096ea1c914d2d12d7','Installed Config4 contract changed')
    libraries = {'libhccl.so':'25dc451dc22780a11c0dd58065a5f90fece8945986c756aa80831265ce261807','libhccl_v2.so':'1b3a4d8a1d09e03f036ca4371c4304f4deb0ee374c2527affaac7623e81bdb04','libhcomm.so':'e1ebc2e879cc923d1e2b310706a15ebb7250bde6644780875584ce5af3cb530c'}
    for name, expected in libraries.items(): require(sha(CANN/'lib64'/name)==expected,'Qualified communication library changed')
    result['communication_libraries_sha256'] = libraries
    result.update(qualification_reused=True,qualification_receipt_sha256=QUAL_SHA,qualification_environment_key=qual['environment_key'])
    require(sha(EXP/'p2.zip')==BUNDLE_SHA,'P2 payload SHA gate')
    ab, contents = archive(EXP/'p2.zip','bundle-manifest.json',False,20*1024**2)
    require(len(contents)==153,'P2 member count gate')
    transfer = json.loads((OLD/'transfer.json').read_text())
    require(sha(OLD/'dependencies.zip')==transfer['dependency_zip_sha256'],'Retained dependency ZIP changed')
    dep, deps = archive(OLD/'dependencies.zip','dependency-manifest.json',True,50*1024**2)
    require(dep['revision']=='e28d87deb1e141e23ace5900594218ff47ab2525' and dep['base_revision']=='8fb0be8781bbf2c22bda4766f037dff8133d805d','Retained dependency identity')
    require(dep['base_manifest_canonical_sha256']=='84e55c92211a15ff62fdef49cf6540af05e3170e6d2269beb0b7493ddccdd1bc','Retained base manifest identity')
    require(rawsha(deps['backend/src/tl_templates/ascend/common.h'])=='d6355337873e5f7368c3ec7cb1f22ef0494663e133152120fee20265f03a1780','Pinned backend changed')
    # Reuse only verified source and fixtures; every library and device object is rebuilt here.
    for name, raw in deps.items():
        dst = EXP/'deps'/name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(raw)
    sources = {}
    for variant in ('f9',):
        root = EXP/variant; source = root/'TileXR'
        shutil.copytree(EXP/'deps/TileXR',source)
        shutil.rmtree(source/'examples/tilelang_dispatch')
        sources[variant] = json.loads(contents[variant+'/source-manifest.json'])
        require(len(sources[variant])==123,'Source manifest count')
        for name, expected in sources[variant].items():
            raw = contents[variant+'/source/'+name]; require(rawsha(raw)==expected,'Source mismatch')
            dst = source/name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(raw)
        save(root/'source-manifest.json',sources[variant])
        for name, expected in HOST.items(): require(sha(source/'examples/tilelang_dispatch'/name)==expected,'Host overlay mismatch')
        for name, raw in contents.items():
            if name.startswith(variant+'/generated/'):
                dst = EXP/name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(raw)
        for tokens in (256,):
            for rank in range(2):
                gen = root/'generated'/('t'+str(tokens))/('rank'+str(rank))
                meta = json.loads((gen/'export.json').read_text()); adapt = json.loads((gen/'adaptation.json').read_text())
                require((meta['world_size'],meta['experts'],meta['rank'],meta['cores'],meta['tokens_per_rank'])==(2,4,rank,64,tokens) and meta['variant']=='p1p2','Export shape gate')
                require(adapt['abi']=={'version':2,'args_size':104} and adapt['args_bytes_aarch64']==104,'Export ABI gate')
                require(adapt['modules'][0]['ub_bytes']==meta['receive_ub_plan']['planned_bytes']<=194560 and adapt['modules'][0]['marker_slots']==52,'Export UB/marker gate')
                require(meta['backend_revision']=='557b11b8b132c6d6ecd8d5b56b74785783c9cc1a' and meta['backend_diff_sha256']=='05f56abcc9e90f5fad157e46e839d672bfec798c437c888f471cd055a6d56956','Export backend gate')
                for name, expected in meta['source_hashes'].items(): require(sha(source/name)==expected,'Export source identity gate')
                for name, expected in adapt['hashes'].items(): require(sha(gen/name)==expected,'Export content gate')
    prep = json.loads(contents['preparation.json'])
    require(prep['revision']==REV and prep['cores']==[64,64] and len(prep['exports'])==2,'Fresh P2 export identity')
    require(prep['expected_output_rows']==[512,512] and not prep['algorithm_modified'],'P2 business shape')
    fixtures = {}
    for name, raw in contents.items():
        if name.startswith('fixture/'):
            dst = EXP/name; dst.parent.mkdir(parents=True,exist_ok=True); dst.write_bytes(raw); fixtures[name] = rawsha(raw)
    identity = json.loads(contents['input-identity.json'])
    require(len(identity['business_files'])==12,'Fixed business input count')
    for name, expected in identity['business_files'].items():
        path = EXP/'fixture'/name
        require(sha(path)==expected['sha256'] and path.stat().st_size==expected['bytes'],'Fixed business input mismatch: '+name)
    case = json.loads((EXP/'fixture/case.json').read_text())
    require(case['cores_per_rank']==[64,64] and case['expected_valid_rows_by_rank']==[512,512],'Fresh descriptor shape')
    require(case['shape']=={'world':2,'experts':4,'tokens_per_rank':256,'hidden':256,'topk':2},'Fixture shape')
    for name, expected in case['hashes'].items(): require(sha(EXP/'fixture'/name)==expected,'Fixture SHA: '+name)
    require(len(fixtures)==15,'P2 fixture count')
    save(EXP/'fixture-hashes.json',fixtures)
    save(EXP/'input-identity.json',identity)
    # New live process/occupancy/health/ports gate, immediately before ACL core query.
    run('build-idle-gate',['/usr/bin/python3',EXP/'build_idle_gate.py'],100,cann=False)
    idle = json.loads((EXP/'build-idle-gate/summary.json').read_text())
    require(idle['status']=='PASS' and idle['devices']==[0,1],'Fresh device gate')
    result['build_idle_gate']='PASS'
    # Host query only: no SetDevice, stream creation, collective or kernel execution.
    probe = '#include <acl/acl.h>\n#include <cstdio>\nint main(){int rc=aclInit(nullptr);std::printf("init %d\\n",rc);if(rc)return 1;bool good=true;for(unsigned d=0;d<2;++d){int64_t v=0;int q=aclrtGetDeviceInfo(d,ACL_DEV_ATTR_VECTOR_CORE_NUM,&v);std::printf("core %u %d %lld\\n",d,q,(long long)v);good=good&&q==0&&v==64;}int f=aclFinalize();std::printf("final %d\\n",f);return good&&f==0?0:1;}\n'
    (EXP/'core-query.cpp').write_text(probe)
    run('core-query-compile',['g++','-std=c++14',EXP/'core-query.cpp','-I'+str(CANN/'include'),'-L'+str(CANN/'lib64'),'-Wl,-rpath,'+str(CANN/'lib64'),'-lascendcl','-o',EXP/'core-query'],30)
    result['device_runtime_probe'] = True
    run('core-query',[EXP/'core-query'],30)
    require((EXP/'core-query.log').read_text().splitlines()==['init 0']+[f'core {d} 0 64' for d in range(2)]+['final 0'],'Live core query output gate')
    core = EXP/'core-install'; build = EXP/'core-build'
    run('core-configure',['cmake','-S',EXP/'deps/TileXR','-B',build,'-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_PREFIX='+str(core),'-DCMAKE_INSTALL_LIBDIR=lib64','-DBUILD_TESTING=OFF','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_EP=OFF','-DTILEXR_BUILD_CHECKER=OFF'],60)
    run('core-build',['cmake','--build',build,'-j4'],180)
    run('core-install',['cmake','--install',build],30)
    result['core_library_sha256'] = sha(core/'lib64/libtile-comm.so')
    artifacts = {}
    for variant in ('f9',):
        for tokens in (256,):
            root = EXP/variant; v = root/'builds'/('t'+str(tokens)); label = variant+'-t'+str(tokens)
            args = ['cmake','-S',root/'TileXR/examples/tilelang_dispatch','-B',v/'build','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_LIBDIR=lib64','-DCMAKE_INSTALL_PREFIX='+str(v/'install'),'-DTL_CANN_ROOT='+str(CANN),'-DTL_BACKEND_ROOT='+str(EXP/'deps/backend'),'-DTL_COMM_PREFIX='+str(core),'-DTL_PRE_ALLREDUCE_SUPPORT=ON','-DTL_READY_PROBE=OFF','-DTL_DISPATCH_WORLD_SIZE=2','-DTL_DISPATCH_EXPERTS=4','-DTL_DISPATCH_CORES_PER_RANK=64;64','-DTL_TOKENS_PER_RANK='+str(tokens),'-DTL_GENERATED_ROOT='+str(root/'generated'/('t'+str(tokens)))]
            run(label+'-configure',args,45); run(label+'-build',['cmake','--build',v/'build','-j4'],180); run(label+'-install',['cmake','--install',v/'build'],25)
            install = v/'install'
            require(len(list((install/'bin').glob('tilelang_dispatch_integrated_r*_profile?')))==4,'Host binary count')
            require(len(list((install/'lib64/tilexr/kernels').glob('tilelang_dispatch_r*_c64_profile?.o')))==4,'Linked device count')
            require(len(list((install/'lib64/tilexr/kernels').glob('*_rel.o')))==4,'Relocatable device count')
            for p in sorted(install.rglob('*')):
                if not p.is_file(): continue
                raw = p.read_bytes(); require(raw[:4]==b'\x7fELF' and raw[5]==1,'Artifact ELF gate')
                if p.suffix=='.o': require(int.from_bytes(raw[16:18],'little')==(1 if p.name.endswith('_rel.o') else 2),'Device ELF type')
                else:
                    require(int.from_bytes(raw[18:20],'little')==183,'Host ELF machine')
                    run(label+'-'+p.name+'-ldd',['ldd',p],10)
                    require(b'not found' not in (EXP/(label+'-'+p.name+'-ldd.log')).read_bytes() and b'devlib' not in (EXP/(label+'-'+p.name+'-ldd.log')).read_bytes(),'Runtime library resolution')
                artifacts[str(p.relative_to(EXP))] = sha(p)
    require(len(artifacts)==12,'Complete P2 artifact count')
    artifacts['core-install/lib64/libtile-comm.so'] = result['core_library_sha256']
    dynamic = subprocess.run(['readelf','-d',str(core/'lib64/libtile-comm.so')],capture_output=True,timeout=10)
    require(dynamic.returncode==0 and b'devlib' not in dynamic.stdout,'Core runtime devlib RPATH')
    save(EXP/'artifact-hashes.json',artifacts)
    for variant, manifest in sources.items():
        for name, expected in manifest.items(): require(sha(EXP/variant/'TileXR'/name)==expected,'Source changed during build')
    for name, expected in fixtures.items(): require(sha(EXP/name)==expected,'Fixture changed during build')
    for name, expected in SYSTEM.items(): require(sha(name)==expected,'System configuration changed during build')
    provenance = {'revision':REV,'f9_device_revision':'dfa8036f6d03015dacc49a699642f61e2ecf1552','world_size':2,'experts':4,'tokens_per_rank':256,'bundle_sha256':BUNDLE_SHA,'dependency_zip_sha256':transfer['dependency_zip_sha256'],'host_sha256':HOST,'cores':[64,64],'core_cxx11_abi':0,'fixture_hashes_sha256':sha(EXP/'fixture-hashes.json'),'artifact_manifest_sha256':sha(EXP/'artifact-hashes.json'),'qualification_receipt_sha256':QUAL_SHA,'npu_launch':False,'p4_compiled_artifacts_reused':False,'fresh_export_metadata':prep['exports']}
    save(EXP/'provenance.json',provenance)
    result.update(status='PASS',source_files_per_variant=123,exports_per_variant=2,business_files_verified=12,descriptors_regenerated=True,cores=[64,64],world_size=2,experts=4,tokens_per_rank=256,core_cxx11_abi=0,host_binaries=4,linked_device_elfs=4,relocatable_device_objects=4,bundle_sha256=BUNDLE_SHA,artifact_manifest_sha256=sha(EXP/'artifact-hashes.json'),provenance_sha256=sha(EXP/'provenance.json'),original_configuration_preserved=True,p4_compiled_artifacts_reused=False)

except BaseException as e:
    result.update(error_type=type(e).__name__,error_detail=str(e)[:240])
    if phases and phases[-1]['exit_code']!=0:
        text = (EXP/(phases[-1]['phase']+'.log')).read_text(errors='replace').splitlines()
        result['first_errors'] = [s[:200] for s in text if any(t in s.lower() for t in ('error:','cmake error','undefined reference','fatal'))][:4]
finally:
    # Refusing an existing experiment must not overwrite its saved build result.
    if started_build: save(EXP/'build-summary.json',result)
summary = dict(result); summary['phases'] = [p for p in phases if not p['phase'].endswith('-ldd')]
print(json.dumps(summary,ensure_ascii=True,separators=(',',':')))
raise SystemExit(0 if result['status']=='PASS' else 1)
