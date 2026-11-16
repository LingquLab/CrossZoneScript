"""Verify saved BS256 evidence and reanalyze with frozen CPU analyzers; never launch NPU."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from numeric_projection import project, encode

EXP = Path('/root/tilelang-107-f9-host107-b520293d-20261116-run01')
RUN = EXP / 'capture-t256-20261116-01'
OUT = EXP / 'capture-t256-readonly-numeric-v2-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
WORKER_SHA = '89a6d6da5ff848a94d962e0ee167c9b867a2ad0d0d1e6061696398b740422b0c'
ORIGINAL = {'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523',
 '/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def verify_sources():
    assert sha(EXP/'artifact-hashes.json') == '2e30632f8e71376742c47ca4b1d7bacebee5d8aaea80ae8e78e0324fbddfcfe7'
    assert sha(EXP/'provenance.json') == 'f56caa6f4e589110eefc3a01eeec8e4d37ca4899a879e01f7a790670f155add1'
    provenance = read(EXP/'provenance.json')
    assert provenance['revision'] == REV and provenance['core_cxx11_abi'] == 0
    for name, expected in read(EXP/'artifact-hashes.json').items():
        assert sha(EXP/name) == expected, 'Built artifact changed'
    assert sha(EXP/'ab.zip') == '5a4011c99da6ffcca4c5ce743778f090aae4f4d3a2345f4b4c6191b653984ba9'
    with zipfile.ZipFile(EXP/'ab.zip') as bundle:
        for variant in ('baseline', 'f9'):
            raw = bundle.read(variant+'/source-manifest.json')
            assert (EXP/variant/'source-manifest.json').read_bytes() == raw
            for name, expected in json.loads(raw).items():
                assert sha(EXP/variant/'TileXR'/name) == expected, 'Frozen source changed'
            for name in bundle.namelist():
                if name.startswith(variant+'/generated/'):
                    assert (EXP/name).read_bytes() == bundle.read(name), 'Frozen export changed'
    assert sha(EXP/'fixture-hashes.json') == provenance['fixture_hashes_sha256']
    for name, expected in read(EXP/'fixture-hashes.json').items():
        assert sha(EXP/name) == expected, 'Fixture changed'
    dependency = Path('/root/tilelang-107-multirank-e28d87de-20261116-run01/dependencies.zip')
    assert sha(dependency) == provenance['dependency_zip_sha256']
    with zipfile.ZipFile(dependency) as bundle:
        for name, expected in json.loads(bundle.read('dependency-manifest.json'))['files'].items():
            if name.startswith('TileXR/tools/profiling/python/'):
                for variant in ('baseline','f9'):
                    assert sha(EXP/variant/name) == expected, 'Frozen parser changed'
    assert sha(RUN/'runtime_group.py') == 'b768d8a2f6209fa4a89621cd1b3927bc5cc6472080b33e11e76bf1236eaa7a2f'
    for name, expected in ORIGINAL.items():
        assert sha(name) == expected, 'Original configuration changed'


REANALYZE = '''import sys,json,importlib.util
from pathlib import Path
sys.dont_write_bytecode=True
exp,run,key,variant=sys.argv[1:]
exp,run=Path(exp),Path(run)
sys.path.insert(0,str(exp/variant/'TileXR/examples'))
spec=importlib.util.spec_from_file_location('saved_worker',run/'runtime_group.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d=run/'groups'/key
analysis,rows=m.validate_native(d/'native',exp/'fixture/p4-e8-t256-balanced',exp/variant/'generated/t256',True)
saved=json.loads((d/'analysis.json').read_text())
normalized=json.loads(json.dumps(analysis,allow_nan=False))
def diff_paths(actual,expected,path='$',limit=6):
    if actual==expected:return []
    if isinstance(actual,dict) and isinstance(expected,dict):
        paths=[]
        for key in sorted(set(actual)|set(expected)):
            child=path+'.'+str(key)
            if key not in actual or key not in expected:paths.append(child+': missing key')
            else:paths.extend(diff_paths(actual[key],expected[key],child,limit-len(paths)))
            if len(paths)>=limit:break
        return paths[:limit]
    if isinstance(actual,list) and isinstance(expected,list):
        if len(actual)!=len(expected):return [path+': length differs']
        paths=[]
        for index,(a,b) in enumerate(zip(actual,expected)):
            paths.extend(diff_paths(a,b,path+'['+str(index)+']',limit-len(paths)))
            if len(paths)>=limit:break
        return paths[:limit]
    return [path+': value/type differs']
if normalized!=saved:
    print(json.dumps({'group':key,'status':'FAIL','normalized_diff_paths':diff_paths(normalized,saved)}),flush=True)
    raise AssertionError('Normalized saved analysis differs from frozen CPU reanalysis')
def integer_keys(value):
    if isinstance(value,dict):return sum(type(k) is int for k in value)+sum(integer_keys(v) for v in value.values())
    if isinstance(value,(list,tuple)):return sum(integer_keys(v) for v in value)
    return 0
assert rows==json.loads((d/'rank-validation.json').read_text()),'Rank validation differs'
print(json.dumps({'group':key,'status':'PASS','ranks':len(rows),'capture_checked':analysis['capture_checked'],'direct_python_equal':analysis==saved,'json_normalized_equal':True,'integer_dictionary_keys':integer_keys(analysis)}))
'''


def verify_group(group):
    directory=RUN/'groups'/group['group']
    for name, field in (('group-summary.json','summary_sha256'),('analysis.json','analysis_sha256'),('capture-evidence.json','capture_evidence_sha256')):
        assert sha(directory/name)==group[field], 'Received capture identity mismatch'
    summary=read(directory/'group-summary.json')
    assert summary['status']=='PASS' and summary['revision']==REV and summary['index']==group['index']
    assert summary['devices']==[0,1,2,3] and summary['cores']==[64]*4
    assert all(v['idle'] for v in summary['occupancy_after']) and len(summary['occupancy_after'])==4
    assert all(v['ok'] for v in summary['health_after']) and len(summary['health_after'])==4
    assert not summary.get('error_type') and not summary.get('finalization_error_detail')
    assert sha(directory/'rank-validation.json')==summary['rank_validation_sha256']
    evidence=read(directory/'capture-evidence.json')
    assert len(evidence)==38, 'Unexpected capture evidence inventory'
    for name, expected in evidence.items():
        path=Path(name)
        if path.is_absolute():
            assert path.is_relative_to(EXP/group['variant']/'generated/t256')
        else:
            assert '..' not in path.parts
            path=directory/path
        assert sha(path)==expected, 'Capture evidence changed'
    return read(directory/'analysis.json')


def main():
    sys.dont_write_bytecode=True
    assert sha(RUN/'worker-summary.json')==WORKER_SHA
    worker=read(RUN/'worker-summary.json')
    assert worker['status']=='PASS' and worker['revision']==REV and worker['tokens']==256
    assert worker['fresh_rank_groups']==20 and worker['rank_captures']==80
    groups=worker['groups']
    assert [g['index'] for g in groups]==list(range(73,93))
    assert all(g['status']=='PASS' for g in groups)
    verify_sources()
    OUT.mkdir(exist_ok=False)
    pairs=[]
    cpu_comparisons=[]
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    for group in groups:
        analysis=verify_group(group)
        p=subprocess.run(['/usr/bin/python3','-c',REANALYZE,str(EXP),str(RUN),group['group'],group['variant']],
                         env=env,capture_output=True,timeout=40)
        (OUT/(group['group']+'.cpu.stdout.log')).write_bytes(p.stdout)
        (OUT/(group['group']+'.cpu.stderr.log')).write_bytes(p.stderr)
        assert p.returncode==0, 'Frozen CPU reanalysis failed: '+group['group']
        result=json.loads(p.stdout)
        assert result['status']=='PASS' and result['ranks']==4 and result['capture_checked'] and result['json_normalized_equal']
        cpu_comparisons.append(result)
        assert analysis==verify_group(group)
        pairs.append((group,analysis))
    numeric=project(pairs)
    raw=encode(numeric)
    (OUT/'numeric.json').write_bytes(raw)
    parts=[raw[i:i+16384] for i in range(0,len(raw),16384)]
    manifest={'schema':'tilelang_f9_numeric_chunks.v1','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
              'chunk_bytes':16384,'chunks':[{'index':i,'bytes':len(part),'sha256':hashlib.sha256(part).hexdigest()} for i,part in enumerate(parts)]}
    (OUT/'manifest.json').write_bytes(encode(manifest))
    overview=[]
    for row in numeric['ranks']:
        record={k:row[k] for k in ('variant','rank','us')}
        def brief(value):
            active=[v for v in value['active_workload_mean_ticks_per_round'] if v is not None]
            return {'mean_ticks':value['core_mean_ticks']['mean'],
                    'round_core_max_mean_ticks':value['core_max_ticks']['mean'],
                    'max_ticks':value['core_max_ticks']['max'],
                    'participant_counts':sorted(set(value['participants_per_round'])),
                    'active_workload_mean_ticks':sum(active)/len(active) if active else None}
        record['tick_groups']={category:{name:brief(value) for name,value in row['tick_groups'][category].items()}
                              for category in ('modules','functions')}
        record['payload_clear_ticks']=brief(row['tick_groups']['stages']['F9.payload_clear'])
        overview.append(record)
    verify_sources()
    assert sha(RUN/'worker-summary.json')==WORKER_SHA
    result={'schema':'tilelang_f9_t256_readonly_numeric_result.v1','status':'PASS','read_only_existing_evidence':True,
            'npu_launch':False,'official_hccl_launch':False,'revision':REV,'source_worker_summary_sha256':WORKER_SHA,
            'groups_reanalyzed':20,'rank_captures_reanalyzed':80,'original_wrapper_exit':1,'wrapper_failure':'ProcessLookupError in /proc monitor',
            'clock':'NOT_VERIFIED','units':'internal ticks; ACL/msprof microseconds separately','export_dir':str(OUT),
            'comparison':'Full object after JSON roundtrip; no fields excluded', 'cpu_comparisons':cpu_comparisons,
            'manifest':manifest,'ranks':overview,'round_tails':numeric['round_tails']}
    (OUT/'readonly-validation.json').write_bytes(encode(result))
    output=encode(result)
    assert len(output)<56000, 'Use bounded projection; do not replay runtime'
    print(output.decode())


if __name__=='__main__':
    main()
