"""Verify saved BS256 evidence and reanalyze with frozen CPU analyzers; never launch NPU."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import zipfile
import csv
import io
import tarfile
import statistics
from numeric_projection import project, encode

EXP = Path('/root/tilelang-107-f9-p2-b520293d-20261116-run01')
RUN = EXP / 'p2-capture-t256-20261116-01'
OUT = EXP / 'p2-t256-recovered-export-20261116-01'
REV = 'b520293d5f014fcbd4897a334d50ebf9ca9c601b'
WORKER_SHA = '6d1211c4a84f9472d11470a2459358583ce2eb770dd8861385cc2d61e148df25'
ORIGINAL = {'/etc/hccl_rootinfo.json':'56a0d1eb4c38422fbbdd946b34036ed6496fe36c1be95045d0519f35d69ad523',
 '/usr/local/Ascend/driver/topo/950/atlas_850_1.json':'65e257641adea221a576bab32710f759a59df80b50bc8ef613b38e10c6164db9'}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read(p):
    return json.loads(Path(p).read_text())


def verify_sources():
    assert sha(EXP/'artifact-hashes.json') == 'f9c77fa3454a00c22e41fce57998e100e243c5d15008bcd41adcfb1639332272'
    assert sha(EXP/'provenance.json') == 'd01cd022ae904cd194836875dfa3a618918d5f55b67a1b47c82c6850ce2a5975'
    provenance = read(EXP/'provenance.json')
    assert provenance['revision'] == REV and provenance['core_cxx11_abi'] == 0
    for name, expected in read(EXP/'artifact-hashes.json').items():
        assert sha(EXP/name) == expected, 'Built artifact changed'
    assert sha(EXP/'p2.zip') == '51f8c1655d5583a3edca3f24dd3fba44aeeb53166d7dc1218c12b628d4d398c5'
    with zipfile.ZipFile(EXP/'p2.zip') as bundle:
        for variant in ('f9',):
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
                for variant in ('f9',):
                    assert sha(EXP/variant/name) == expected, 'Frozen parser changed'
    assert sha(RUN/'runtime_group.py') == 'afa64c3e54b1f252f7395975b898f47f7fc6a0a9535f17e78aee32bd6552b86d'
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
analysis,rows=m.validate_native(d/'native',exp/'fixture',exp/variant/'generated/t256',True)
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
    assert summary['devices']==[0,1] and summary['cores']==[64]*2
    assert all(v['idle'] for v in summary['occupancy_after']) and len(summary['occupancy_after'])==2
    assert all(v['ok'] for v in summary['health_after']) and len(summary['health_after'])==2
    assert not summary.get('error_type') and not summary.get('finalization_error_detail')
    assert sha(directory/'rank-validation.json')==summary['rank_validation_sha256']
    evidence=read(directory/'capture-evidence.json')
    assert len(evidence)==20, 'Unexpected capture evidence inventory'
    for name, expected in evidence.items():
        path=Path(name)
        if path.is_absolute():
            assert path.is_relative_to(EXP/group['variant']/'generated/t256')
        else:
            assert '..' not in path.parts
            path=directory/path
        assert sha(path)==expected, 'Capture evidence changed'
    return read(directory/'analysis.json')


BUSINESS={'rank0/x.bin': {'bytes': 131072, 'sha256': 'd3e7c6e45e79cfc25bf6d2b859580db10912dea0049533ed0cb30964f155b145'}, 'rank0/ids.bin': {'bytes': 2048, 'sha256': 'dcee6192d4cf925bcab5f33e78044d53b78d4a61aa3fdef7b5aadf40aabcc8ed'}, 'rank0/expected_output.bin': {'bytes': 262144, 'sha256': '872b92b12465efa523ee6ec5734cecf01a80414bf258e444290692a6a0675bf4'}, 'rank0/expected_assist.bin': {'bytes': 6144, 'sha256': 'dea0bb77756133ec45ed981cbef5bdfd47173a605d26d04b72645ab1a953e2b9'}, 'rank0/expected_counts.bin': {'bytes': 16, 'sha256': '0bae8353cb40dfcf5002b2346a6e132a69153aed7da9092f7d4887be99e17815'}, 'rank0/expected_expert_nums.bin': {'bytes': 16, 'sha256': 'e74a8f30ae1dc51d26f4ebe794a86f49cca071f24928d22547bc209adae7244f'}, 'rank1/x.bin': {'bytes': 131072, 'sha256': '8f5635765d1e6ed1da1cd30d95b14e2c7744a30f9201098aedafb76063ba15b0'}, 'rank1/ids.bin': {'bytes': 2048, 'sha256': 'dcee6192d4cf925bcab5f33e78044d53b78d4a61aa3fdef7b5aadf40aabcc8ed'}, 'rank1/expected_output.bin': {'bytes': 262144, 'sha256': '872b92b12465efa523ee6ec5734cecf01a80414bf258e444290692a6a0675bf4'}, 'rank1/expected_assist.bin': {'bytes': 6144, 'sha256': 'cbbdcbc6797968b269abfd7a2b14ad6b53610c8d451cecc6dc7946e9330c42b8'}, 'rank1/expected_counts.bin': {'bytes': 16, 'sha256': '0bae8353cb40dfcf5002b2346a6e132a69153aed7da9092f7d4887be99e17815'}, 'rank1/expected_expert_nums.bin': {'bytes': 16, 'sha256': 'e74a8f30ae1dc51d26f4ebe794a86f49cca071f24928d22547bc209adae7244f'}}

def csvfile(name,rows):
    stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator='\n');writer.writerows(rows)
    raw=stream.getvalue().encode('ascii');(OUT/name).write_bytes(raw)
    return {'path':str(OUT/name),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


def deliver(numeric,pairs,cpu_comparisons):
    assert len(pairs)==10 and len(numeric['ranks'])==2
    business=BUSINESS
    for name,record in business.items():
        path=EXP/'fixture'/name
        assert path.stat().st_size==record['bytes'] and sha(path)==record['sha256'],'Historical P2 business input differs'
    assert len(business)==12
    rows=[['round','group','rank','category','name','participants','active_cores','mean_ticks','max_ticks','active_mean_ticks','participant_mask','active_mask','workload_json']]
    times=[['round','group','rank','acl_dispatch_us','msprof_dispatch_us','pre_dispatch_event_us','segment_event_us']]
    slow={};round_slow=[['round','group','rank','category','name','max_ticks','max_us_spec_1GHz','cores_json']]
    for group,analysis in pairs:
        for rank,row in enumerate(analysis['rank_results']):
            cap=row['captures'][0]
            for category in ('modules','functions','stages'):
                names=sorted(cap[category]) if category!='stages' else ['F9.payload_clear']
                for name in names:
                    v=cap[category][name];totals={int(c):t for c,t in v['per_core_sum_ticks'].items()}
                    assert sorted(totals)==v['participating_core_ids'] and len(totals)==v['participating_cores']
                    assert v['max_ticks']==max(totals.values())
                    assert abs(v['mean_ticks']-statistics.mean(totals.values()))<1e-9
                    active=v['active_workload_core_ids']
                    assert all(c in totals for c in active)
                    if active:
                        assert abs(v['active_workload_mean_ticks']-statistics.mean(totals[c] for c in active))<1e-9
                    else:
                        assert v['active_workload_mean_ticks'] is None
                    cores=sorted(c for c,t in totals.items() if t==v['max_ticks'])
                    def mask(ids):return format(sum(1<<c for c in ids),'016x')
                    rows.append([group['round'],group['group'],rank,category,name,len(totals),len(active),v['mean_ticks'],v['max_ticks'],v['active_workload_mean_ticks'],mask(totals),mask(active),json.dumps(v['workload'],sort_keys=True,separators=(',',':'))])
                    round_slow.append([group['round'],group['group'],rank,category,name,v['max_ticks'],v['max_ticks']/1000,json.dumps(cores,separators=(',',':'))])
                    key=category+'.'+name
                    sample={'round':group['round'],'group':group['group'],'rank':rank,'cores':cores,'ticks':v['max_ticks'],'us_spec_1GHz':v['max_ticks']/1000}
                    if key not in slow or sample['ticks']>slow[key]['ticks']:slow[key]={'ticks':sample['ticks'],'us_spec_1GHz':sample['ticks']/1000,'samples':[sample]}
                    elif sample['ticks']==slow[key]['ticks']:slow[key]['samples'].append(sample)
    overview=[]
    for row in numeric['ranks']:
        entry={'rank':row['rank'],'us':row['us'],'groups':{}}
        for category in ('modules','functions','stages'):
            names=sorted(row['tick_groups'][category]) if category!='stages' else ['F9.payload_clear']
            for name in names:
                v=row['tick_groups'][category][name];key=category+'.'+name
                active=[x for x in v['active_workload_mean_ticks_per_round'] if x is not None]
                entry['groups'][key]={'participant_mean_ticks':v['core_mean_ticks']['mean'],'participant_mean_us_spec_1GHz':v['core_mean_ticks']['mean']/1000,
                    'round_max_mean_ticks':v['core_max_ticks']['mean'],'max_ticks':v['core_max_ticks']['max'],
                    'active_workload_mean_ticks':statistics.mean(active) if active else None,'participant_counts':sorted(set(v['participants_per_round']))}
        for cap in row['captures']:
            u=cap['us'];times.append([cap['pair_round'],cap['group'],row['rank'],u['acl_dispatch_us'],u['msprof_dispatch_us'],u['pre_dispatch_event_us'],u['segment_event_us']])
        overview.append(entry)
    from numeric_projection import stats
    tails=numeric['round_tails']['f9']
    tail_stats={key:stats([row['rank_max_us'][key] for row in tails]) for key in tails[0]['rank_max_us']}
    inventory={name:{'bytes':(OUT/name).stat().st_size,'sha256':sha(OUT/name)} for name in ('numeric.json',)}
    files={'per_round_stages.csv':csvfile('per_round_stages.csv',rows),'per_round_slowest.csv':csvfile('per_round_slowest.csv',round_slow),'four_us.csv':csvfile('four_us.csv',times)}
    return {'schema':'tilelang_p2_f9_t256_recovered_delivery.v1','status':'PASS','revision':REV,
        'read_only_existing_evidence':True,'npu_launch':False,'official_hccl_launch':False,'clock':'NOT_VERIFIED',
        'target':'141.61.73.107','devices':[0,1],'cores':[64,64],'world_size':2,'experts':4,'tokens_per_rank':256,'hidden':256,'topk':2,'dtype':'BF16','routing':'balanced',
        'source_worker_summary_sha256':WORKER_SHA,'groups_reanalyzed':10,'rank_captures_reanalyzed':20,'cpu_comparisons':cpu_comparisons,
        'original_wrapper_exit':1,'original_parent_final_receipt':'ABSENT','namespace_exit':'NOT_RECORDED',
        'wrapper_failure':'ProcessLookupError in /proc monitor; original wrapper remains failed; validation is recovered independently',
        'groups':numeric['identity'],'business_files_verified':12,'business_files':business,
        'artifact_manifest_sha256':sha(EXP/'artifact-hashes.json'),'provenance_sha256':sha(EXP/'provenance.json'),
        'original_configuration_preserved':True,'export_dir':str(OUT),'files':files,'numeric':inventory['numeric.json'],
        'units':'ticks retained; stage us=ticks/1000 per 950DT 1GHz specification, not measured calibration; ACL/msprof already us',
        'aggregation':'equal ten fresh rounds per rank; means use actual participants; module durations compose intervals on the same core; no sum of means across core sets',
        'pre_dispatch_scope':'AllReduce plus event boundaries; not pure HCCL kernel duration','tails_scope':'max reported rank duration per metric, not globally aligned interval',
        'ranks':overview,'slowest_actual_core':slow,'rank_max_four_us':tail_stats,'round_rank_max_four_us':[{k:v for k,v in row.items() if k in ('pair_round','rank_max_us')} for row in tails]}


def main():
    sys.dont_write_bytecode=True
    assert sha(RUN/'worker-summary.json')==WORKER_SHA
    worker=read(RUN/'worker-summary.json')
    assert worker['status']=='PASS' and worker['revision']==REV and worker['tokens']==256
    assert worker['devices']==[0,1] and worker['cores']==[64,64]
    assert worker['fresh_rank_groups']==10 and worker['rank_captures']==20
    assert worker['rank_receipts']==20 and worker['rank_dispatch_iterations']==420 and worker['allreduce_values_checked']==440
    assert worker['profiling'] is True and worker['warmup_per_group']==20 and worker['measured_per_group']==1
    assert not (RUN/'summary.json').exists(),'Original receipt changed; inspect before recovery'
    previous=read(EXP/'p2-correctness-20261116-01/summary.json')
    assert previous['status']=='PASS' and previous['fresh_rank_groups']==3 and previous['allreduce_values_checked']==132
    for g in previous['groups']:
        d=EXP/'p2-correctness-20261116-01/groups'/g['group']
        assert sha(d/'group-summary.json')==g['summary_sha256'] and sha(d/'analysis.json')==g['analysis_sha256']
    groups=worker['groups']
    assert [g['index'] for g in groups]==list(range(4,14))
    assert all(g['status']=='PASS' and g['variant']=='f9' and g['case']=='p2-e4-t256-balanced' for g in groups)
    assert [g['round'] for g in groups]==list(range(1,11))
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
        assert result['status']=='PASS' and result['ranks']==2 and result['capture_checked'] and result['json_normalized_equal']
        cpu_comparisons.append(result)
        assert analysis==verify_group(group)
        pairs.append((group,analysis))
    numeric=project(pairs)
    raw=encode(numeric)
    (OUT/'numeric.json').write_bytes(raw)
    result=deliver(numeric,pairs,cpu_comparisons)
    verify_sources()
    assert sha(RUN/'worker-summary.json')==WORKER_SHA
    (OUT/'validation-summary.json').write_bytes(encode(result))
    members=['p2.zip','provenance.json','artifact-hashes.json','fixture-hashes.json','build-summary.json',
             'fixture','p2-correctness-20261116-01','p2-capture-t256-20261116-01',
             'f9/generated/t256','f9/builds/t256/install','core-install','f9/TileXR/tools/profiling/python',OUT.name]
    archive=EXP/'P2-F9-T256-b520-validated-evidence-20261116-01.tar.gz'
    assert not archive.exists(),'Evidence archive exists; inspect without overwrite'
    with archive.open('xb') as stream:
        with tarfile.open(fileobj=stream,mode='w:gz',compresslevel=2) as bundle:
            for name in members:bundle.add(EXP/name,arcname=name,recursive=True)
    with tarfile.open(archive,'r:gz') as bundle:
        names=bundle.getnames()
        records=[name for name in names if name.startswith('p2-capture-t256-20261116-01/groups/') and name.endswith('/round20.dispatch.records')]
        assert len(records)==20
        assert hashlib.sha256(bundle.extractfile(OUT.name+'/numeric.json').read()).hexdigest()==sha(OUT/'numeric.json')
    result['archive']={'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'rank_capture_record_files':len(records),'numeric_sha_verified':True}
    (OUT/'delivery-receipt.json').write_bytes(encode(result))
    text=encode(result);assert len(text)<45000,'Read saved compact delivery; no runtime replay'
    print(text.decode())


if __name__=='__main__':
    main()
