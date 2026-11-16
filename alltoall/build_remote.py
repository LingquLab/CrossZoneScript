"""Container build implementation used only through build.py.
容器内构建实现，由 build.py 提供归档及路径参数。
"""
import base64,hashlib,io,json,pathlib,subprocess,tarfile
rev=REV_VALUE
b=pathlib.Path(ROOT_VALUE)
old=pathlib.Path(OLD_VALUE)
assert not b.exists(), 'preserve existing evidence'
b.mkdir();s=b/'source';s.mkdir()
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(ARCHIVE_VALUE))) as tar:
    for m in tar.getmembers():
        assert not m.issym() and not m.islnk() and not pathlib.PurePosixPath(m.name).is_absolute() and '..' not in pathlib.PurePosixPath(m.name).parts
    tar.extractall(s)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for name,cmd in [('configure',['cmake','-S',str(s/'tests/collectives/memory'),'-B',str(b/'build'),'-DTILEXR_MEMORY_BUILD_SIMULATOR=ON','-DASCEND_HOME_PATH=/opt/sim/Ascend/cann-9.1.0']),('compile',['cmake','--build',str(b/'build'),'--target','tilexr_alltoall_mem_sim','tilexr_alltoall_direct_two_group','tilexr_alltoall_direct_multi_peer','dump_alltoall_mem_layout','-j4'])]:
    with (b/(name+'.log')).open('wb') as log:
        p=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=240)
    if p.returncode:
        print(json.dumps(dict(status=name+'_failed',errors=[r[:400] for r in (b/(name+'.log')).read_text(errors='replace').splitlines() if 'error:' in r][:5])))
        raise SystemExit(1)
layout=b/'layout.json';layout.write_bytes(subprocess.check_output([str(b/'build/dump_alltoall_mem_layout')]))
l=json.loads(layout.read_text());assert l['kernel_args_size']==96 and l['max_ranks']>=64
env=json.loads((old/'environment-report.json').read_text());env['source_root']=str(s)
for path,sha in env['files'].items():assert digest(pathlib.Path(path))==sha
(b/'environment-report.json').write_text(json.dumps(env,indent=2))
files={str(p.relative_to(s)):digest(p) for p in s.rglob('*') if p.is_file()}
base=json.loads((old/'build-report.json').read_text())
base.update(runtime_commit=rev,source_files=files)
base['layout'].update(path=str(layout),sha256=digest(layout))
runner0=(old/'single-retry20261116.py').read_text()
variants=[('memory','tilexr_alltoall_mem_sim.o',None),('group','tilexr_alltoall_direct_two_group.o',None)]+[('multi-b'+str(n),'tilexr_alltoall_direct_multi_peer.o',n) for n in (1,2,4,8,16,32)]
rows=[]
for name,obj,blocks in variants:
    report=json.loads(json.dumps(base));kernel=b/'build'/obj
    variant='memory' if name=='memory' else 'direct-two-group' if name=='group' else 'direct-multi-peer'
    report['kernel'].update(path=str(kernel),sha256=digest(kernel),entry='TileXRAllToAllMemSim',variant=variant)
    if blocks:report['kernel']['block_dim']=blocks
    (b/('build-'+name+'.json')).write_text(json.dumps(report,indent=2))
    runner=runner0.replace(str(old),str(b)).replace('df3d2601f38c7973bd70fe91ac6a57905d0dc4f2',rev).replace('a98630a40445c21ef9aa9c675f1773e1cac758fa8a298be3f2aa396a8e92aa5f',digest(kernel)).replace('df3d2601',rev[:8]).replace('-retry20261116','-rebase-'+name+'-20261116').replace('build-report.json','build-'+name+'.json')
    runner=runner.replace('(32,64)','(8,16,32,64)').replace("('ordered','data-as-flag','sentinel')","('ordered','data-as-flag','sentinel','two-group')")
    compile(runner,'run-'+name+'.py','exec');(b/('run-'+name+'.py')).write_text(runner)
    rows.append(dict(variant=variant,block_dim=blocks,kernel_sha256=digest(kernel),kernel_bytes=kernel.stat().st_size,runner=str(b/('run-'+name+'.py'))))
summary=dict(status='compiled',runtime_commit=rev,main='0ad30f8dfee42e49232afa0242e9f29b941bdaf4',layout_sha256=digest(layout),comm_args_size=l['comm_args_size'],environment_assets=len(env['files']),records=rows,simulation_launched=False)
(b/'build-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
