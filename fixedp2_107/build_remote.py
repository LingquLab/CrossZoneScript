"""Fixed P2 F4/F8 independent ABI0 build; no collective or device kernel launch."""
from pathlib import Path,PurePosixPath
import hashlib,json,os,shutil,stat,struct,subprocess,time,zipfile
from operations import ROOT,CANN,sha,save,require,run,integrity,gate,SYSTEM
CANDIDATE='083ce35ee3847f11a3c42222b05167275ffecdbd'
REV='a4b8257a44f78849cf2d2db37cccffee9fb295e4'
PAYLOAD='1e051e143d954eb38a45e3b7e4f072a9252ea688da65e36b7869716112ed43ea'
OLD=Path('/root/tilelang-107-multirank-e28d87de-20261116-run01')

def unpack(path,destination,manifest_name=None,expected=None):
    if expected:require(sha(path)==expected,'Archive SHA gate')
    with zipfile.ZipFile(path) as z:
        infos=z.infolist();names=z.namelist()
        require(len(names)==len(set(names)) and z.testzip() is None,'Archive membership/CRC')
        require(len(names)<6000 and sum(i.file_size for i in infos)<180*1024**2,'Archive size gate')
        for i in infos:
            p=PurePosixPath(i.filename)
            require(not p.is_absolute() and '..' not in p.parts and not stat.S_ISLNK(i.external_attr>>16),'Unsafe archive')
        if manifest_name:
            m=json.loads(z.read(manifest_name));files=m.get('files',m)
            require(set(names)==set(files)|{manifest_name},'Unmanifested member')
            for name,digest in files.items():require(hashlib.sha256(z.read(name)).hexdigest()==digest,'Member SHA gate')
        else:m=None
        z.extractall(destination)
    return m

def main():
    started=time.monotonic()
    result={'schema':'tilelang_fixedp2_107_build.v1','candidate':CANDIDATE,'experiment_revision':REV,'status':'FAIL_OR_UNVERIFIED','npu_launch':False,'device_runtime_probe':False,'clock':'NOT_VERIFIED','directory':str(ROOT)}
    try:
        require(not (ROOT/'build-started.json').exists(),'Build already attempted: audit saved state')
        require(os.geteuid()==0 and sha('/etc/machine-id')=='78020a81dd869c8ca5a7ba9f4a18b25a9fead957b807f74e65793fced37b9af0','Target identity')
        save(ROOT/'build-started.json',{'candidate':CANDIDATE,'experiment_revision':REV})
        require(shutil.disk_usage(ROOT).free>3*1024**3,'Free disk gate')
        require(not os.environ.get('LD_PRELOAD') and not os.environ.get('ASCEND_VISIBLE_DEVICES') and not os.environ.get('ASCEND_RT_VISIBLE_DEVICES'),'Device environment override')
        unpack(ROOT/'payload.zip',ROOT/'payload','bundle-manifest.json',PAYLOAD)
        payload=ROOT/'payload'
        shutil.copytree(payload/'fixture',ROOT/'fixture');shutil.copy2(payload/'input-identity.json',ROOT/'input-identity.json')
        result['integrity']=integrity()
        ports=[p for i in range(13) for p in (*range(28000+64*i,28032+64*i),*range(30000+3*i,30003+3*i))]
        result['idle_gate']=gate(ROOT/'build-gate',ports=ports)
        # Only verified source dependencies are reused, never linked artifacts.
        transfer=json.loads((OLD/'transfer.json').read_text());dep=OLD/'dependencies.zip'
        m=unpack(dep,ROOT/'deps','dependency-manifest.json',transfer['dependency_zip_sha256'])
        require(m['revision']=='e28d87deb1e141e23ace5900594218ff47ab2525' and m['base_revision']=='8fb0be8781bbf2c22bda4766f037dff8133d805d','Dependency identity')
        require(m['base_manifest_canonical_sha256']=='84e55c92211a15ff62fdef49cf6540af05e3170e6d2269beb0b7493ddccdd1bc','Base dependency provenance')
        require(sha(ROOT/'deps/backend/src/tl_templates/ascend/common.h')=='d6355337873e5f7368c3ec7cb1f22ef0494663e133152120fee20265f03a1780','Backend template identity')
        source=ROOT/'source';shutil.copytree(ROOT/'deps/TileXR',source)
        unpack(payload/'spdlog-source.zip',source/'3rdparty/spdlog',expected='f4f3d720e250a93844723eecc68cddd06925994e7bc48dce305f17e91af625e6')
        require((source/'3rdparty/spdlog/include/spdlog/spdlog.h').is_file(),'Missing spdlog')
        shutil.rmtree(source/'examples/tilelang_dispatch')
        shutil.copytree(payload/'source/examples/tilelang_dispatch',source/'examples/tilelang_dispatch')
        sources=json.loads((payload/'source-manifest.json').read_text())
        for name,digest in sources.items():require(sha(source/name)==digest,'Candidate source identity')
        prep=json.loads((payload/'preparation.json').read_text())
        require(prep['experiment_revision']==REV and prep['candidate']==CANDIDATE and prep['host_abi_bytes']==112,'Candidate metadata')
        shutil.copytree(payload/'generated',ROOT/'generated')
        for rank in (0,1):
            gen=ROOT/'generated'/f'rank{rank}';meta=json.loads((gen/'export.json').read_text());adapt=json.loads((gen/'adaptation.json').read_text())
            require((meta['rank'],meta['cores'],meta['tokens_per_rank'],meta['variant'])==(rank,64,256,'p1p2'),'Actual export shape')
            require(adapt['args_bytes_aarch64']==112 and adapt['modules'][0]['marker_slots']==52,'Host/profile ABI')
            require(adapt['modules'][0]['ub_bytes']==191264<=meta['receive_ub_plan']['planned_bytes']<=194560,'UB budget')
            for name,digest in meta['source_hashes'].items():require(sha(source/name)==digest,'Export/source binding')
            for name,digest in adapt['hashes'].items():require(sha(gen/name)==digest,'Generated content binding')
        # Live host-only core query after the occupancy/health gate; no SetDevice/stream/kernel.
        (ROOT/'query.cpp').write_text('#include <acl/acl.h>\n#include <cstdio>\nint main(){if(aclInit(nullptr))return 1;bool ok=true;for(unsigned d=0;d<2;++d){int64_t n=0;int e=aclrtGetDeviceInfo(d,ACL_DEV_ATTR_VECTOR_CORE_NUM,&n);printf("%u %d %lld\\n",d,e,(long long)n);ok=ok&&e==0&&n==64;}return aclFinalize()==0&&ok?0:2;}\n')
        run(ROOT,'query-compile',['g++',ROOT/'query.cpp','-I'+str(CANN/'include'),'-L'+str(CANN/'lib64'),'-Wl,-rpath,'+str(CANN/'lib64'),'-lascendcl','-o',ROOT/'query'],30)
        result['device_runtime_probe']=True
        run(ROOT,'query',[ROOT/'query'],30)
        require((ROOT/'query.log').read_text().splitlines()==['0 0 64','1 0 64'],'Actual per-device core counts')
        core=ROOT/'core-install'
        run(ROOT,'core-configure',['cmake','-S',source,'-B',ROOT/'core-build','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_PREFIX='+str(core),'-DCMAKE_INSTALL_LIBDIR=lib64','-DBUILD_TESTING=OFF','-DTILEXR_BUILD_TESTS=OFF','-DTILEXR_BUILD_COLLECTIVES=OFF','-DTILEXR_BUILD_EP=OFF','-DTILEXR_BUILD_CHECKER=OFF'],60)
        run(ROOT,'core-build',['cmake','--build',ROOT/'core-build','--verbose','-j4'],240)
        run(ROOT,'core-install',['cmake','--install',ROOT/'core-build'],30)
        install=ROOT/'install'
        run(ROOT,'runner-configure',['cmake','-S',source/'examples/tilelang_dispatch','-B',ROOT/'build','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_CXX_FLAGS=-D_GLIBCXX_USE_CXX11_ABI=0','-DCMAKE_INSTALL_LIBDIR=lib64','-DCMAKE_INSTALL_PREFIX='+str(install),'-DTL_CANN_ROOT='+str(CANN),'-DTL_BACKEND_ROOT='+str(ROOT/'deps/backend'),'-DTL_COMM_PREFIX='+str(core),'-DTL_PRE_ALLREDUCE_SUPPORT=ON','-DTL_READY_PROBE=OFF','-DTL_TOKENS_PER_RANK=256','-DTL_GENERATED_ROOT='+str(ROOT/'generated')],60)
        run(ROOT,'runner-build',['cmake','--build',ROOT/'build','--verbose','-j4'],240)
        run(ROOT,'runner-install',['cmake','--install',ROOT/'build'],30)
        bins=list((install/'bin').glob('tilelang_dispatch_integrated_r*_profile?'))
        devs=list((install/'lib64/tilexr/kernels').glob('tilelang_dispatch_r*_c64_profile?.o'))
        rels=list((install/'lib64/tilexr/kernels').glob('*_rel.o'))
        require((len(bins),len(devs),len(rels))==(4,4,4),'Independent P2 build artifact counts')
        artifacts={}
        for p in [*bins,*devs,*rels,core/'lib64/libtile-comm.so']:
            b=p.read_bytes();require(b[:4]==b'\x7fELF' and b[5]==1,'ELF format')
            if p.suffix=='.o':require(int.from_bytes(b[16:18],'little')==(1 if p.name.endswith('_rel.o') else 2),'Device ELF type')
            else:
                require(int.from_bytes(b[18:20],'little')==183,'Host aarch64')
                label='ldd-'+p.name;run(ROOT,label,['ldd',p],15)
                require(b'not found' not in (ROOT/(label+'.log')).read_bytes() and b'devlib' not in (ROOT/(label+'.log')).read_bytes(),'Host dependency resolution')
            artifacts[str(p.relative_to(ROOT))]=sha(p)
        save(ROOT/'artifact-hashes.json',artifacts)
        for name,digest in sources.items():require(sha(source/name)==digest,'Source changed during build')
        result['integrity_after']=integrity()
        result.update(status='PASS',cores=[64,64],source_files=len(sources),source_manifest_sha256=sha(payload/'source-manifest.json'),bundle_sha256=PAYLOAD,dependency_zip_sha256=sha(dep),artifact_manifest_sha256=sha(ROOT/'artifact-hashes.json'),compiled_artifacts=artifacts,host_abi_bytes=112,marker_slots=52,ub_bytes=191264,batch_capacity=91,core_cxx11_abi=0,old_elf_reused=False,runtime_validation='NOT_RUN')
    except Exception as e:result.update(error_type=type(e).__name__,error_detail=str(e)[:240])
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    save(ROOT/'build-summary.json',result)
    print(json.dumps(result,separators=(',',':')))
    return 0 if result['status']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
