"""Audit complete matrix against original reports and process receipts.
按原始报告和进程回执核验完整矩阵，不重新启动仿真。
"""
import hashlib,json,pathlib,sys
root=pathlib.Path(ROOT_VALUE)
rev=REV_VALUE
s=root/'source';sys.path.insert(0,str(s/'tools/unitest'))
from controller import memory_sst as mem
sizes=('small','512k','4m')
expected={(n,size,'memory',alg) for n in (8,16,32,64) for size in sizes for alg in ('ordered','data-as-flag','sentinel')}
expected|={(64,size,'group','two-group') for size in sizes}
expected|={(32,size,'multi-b'+str(b),'sentinel') for b in (1,2,4,8,16,32) for size in sizes}
rows=[json.loads(p.read_text()) for p in (root/'regression').glob('*/summary.json')]
identity=lambda r:(r['ranks'],r['size'],r['variant'],r['algorithm'])
assert len(rows)==57 and {identity(r) for r in rows}==expected
by_variant={}
compact=[]
for row in sorted(rows,key=lambda r:(r['variant'],r['ranks'],sizes.index(r['size']),r['algorithm'])):
    key=identity(row);n,size,variant,alg=key
    report=json.loads(pathlib.Path(row['report']).read_text())
    cfg=report['simulator'];build,env,layout=mem.qualify(cfg)
    assert build['runtime_commit']==rev==row['runtime_commit']
    assert build['kernel']['sha256']==row['kernel_sha256']
    assert cfg['iterations']==1 and not report['run_options']['profiling']['enabled']
    total=n*512 if size=='small' else 524288 if size=='512k' else 4194304
    assert total==row['total_bytes_per_rank'] and total//n==row['bytes_per_peer']
    expected_blocks=64 if variant=='group' else int(variant.split('b')[-1]) if variant.startswith('multi') else min(n,32)
    prepare=report['phases']['prepare']
    assert row['block_dim']==prepare['block_dim']==expected_blocks
    assert prepare['kernel_variant']==build['kernel']['variant']
    suite=pathlib.Path(cfg['suite_output']);receipt=json.loads((suite/'process_receipt.json').read_text())
    assert receipt['remaining_processes']==[] and receipt['returncode']==row['returncode']
    assert mem.digest(suite/'kernel.o')==row['kernel_sha256']
    observed=receipt['observed_libraries']
    for name,path in env['libraries'].items():
        match=[v for p,v in observed.items() if pathlib.Path(p).name==name]
        assert len(match)==1 and match[0]['path']==path and match[0]['sha256']==env['files'][path]
    ranks=report.get('operator_result',{}).get('ranks',[])
    valid=report['status']=='passed' and receipt['returncode']==0 and len(ranks)==n and {r['rank'] for r in ranks}==set(range(n)) and all(r['status']=='passed' and r['guards']=='passed' and r['padding']=='passed' and r['checked_bytes']==total for r in ranks)
    assert bool(valid)==row['validated']
    if valid:assert row['simulated_us']>0 and row['checked_ranks']==n
    else:assert report['failed_phase']=='simulation' and row['simulated_us'] is None
    by_variant.setdefault(variant,dict(passed=0,total=0));by_variant[variant]['total']+=1;by_variant[variant]['passed']+=bool(valid)
    compact.append(dict(ranks=n,size=size,variant=variant,algorithm=alg,block_dim=expected_blocks,validated=bool(valid),simulated_us=row['simulated_us'],error=row['error'],returncode=row['returncode']))
summary=dict(status='matrix_audited',runtime_commit=rev,main='0ad30f8dfee42e49232afa0242e9f29b941bdaf4',case_count=len(rows),passed=sum(r['validated'] for r in rows),failed=sum(not r['validated'] for r in rows),by_variant=by_variant,source_hashes_verified=True,deployed_kernels_verified=True,launch_geometry_verified=True,loaded_libraries_verified=True,all_owned_processes_clean=True,records=compact)
(root/'final-audit.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
