"""Reparse only saved 4-variant evidence on CPU; never query devices or launch ACL."""
from pathlib import Path
import csv,hashlib,json,math,sys,zipfile
ROOT=Path('/root/tilelang-107-targeted-p2-20261117-run01')
OUT=ROOT/'validated-export01'
sys.path.insert(0,str(ROOT))
from operations import integrity,require,save,sha
from statistics_cpu import aggregate,capture_statistics,distribution
BUILD_SHA='e7c80dc768fe044aa99b11a555fdf2ed13d415899d56a617b76129367bbe295a'
CORRECTNESS_SHA='7abc97af9a4f1d9119a1fca7426f678b28aab6cbdb9d972acbb2024d88d68cea'
PARENT_SHA='81d83525904ee1bc80c9532d457e5e5b5e34bc7985486cdef62ec531c8ff536b'
REVISION='e788bc7214781112ee890c0771868227e713c9f6'
VARIANTS=('B','U','R64','R128')
FOUR_US=('dispatch_acl_us','msprof_dispatch_us','pre_dispatch_event_us','segment_event_us')
WORK=('received_records','received_wire_bytes','sent_records','sent_wire_bytes','count_polls','gate_polls','payload_polls','receive_states','route_ids_read')
PRIMARY=('stages.module.whole.all','modules.prepare.all','modules.count_publish.all','modules.pack_send.all','modules.count_prefix.all','modules.receive_output.receiver','modules.receive_output.non_receiver','functions.F7.receiver','functions.F7.non_receiver','functions.F8.receiver','functions.F9.receiver','compositions.F8+F9.receiver')

def plans(phase):
    rows=[]
    for r in range(3 if phase=='correctness' else 10):
        order=VARIANTS[r%4:]+VARIANTS[:r%4]
        for variant in order:
            index=(0 if phase=='correctness' else 12)+len(rows)
            rows.append(dict(index=index,ordinal=index+1,phase_ordinal=len(rows)+1,round=r+1,variant=variant,hccl_base_port=20000+64*index,host_base_port=31000+3*index))
    return rows

def csv_file(name,header,rows):
    with (OUT/name).open('x',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(header);writer.writerows(rows)

def source_check(build):
    require(sha(ROOT/'build-summary.json')==BUILD_SHA,'Build receipt changed')
    identity=json.loads((ROOT/'experiment.json').read_text())
    require(build['status']=='PASS' and build['candidate']==identity['candidate'] and build['experiment_revision']==identity['experiment_revision']==REVISION and build['cores']==[64,64] and build['host_abi_bytes']==112 and build['marker_slots']==52 and build['variants']==list(VARIANTS),'Wrong four-variant build')
    require([v['name'] for v in build['variant_metadata']]==list(VARIANTS),'Variant metadata order')
    for name,digest in build['compiled_artifacts'].items():require(sha(ROOT/name)==digest,'Compiled artifact changed')
    require(sha(ROOT/'payload.zip')==build['bundle_sha256'],'Payload changed')
    with zipfile.ZipFile(ROOT/'payload.zip') as z:
        manifest=json.loads(z.read('bundle-manifest.json'))
        require('experiment.json' in manifest,'Frozen identity not manifested')
        for name,digest in manifest.items():
            if name.startswith(('source/','fixture/','generated/')) or name in ('input-identity.json','experiment.json'):
                require(sha(ROOT/name)==digest,'Frozen source/input changed: '+name)
    require(sha(ROOT/'payload/source-manifest.json')==build['source_manifest_sha256'],'Source manifest changed')
    require(sha(ROOT/'artifact-hashes.json')==build['artifact_manifest_sha256'],'Artifact manifest changed')
    for name,digest in json.loads((ROOT/'runtime-manifest.json').read_text()).items():require(sha(ROOT/name)==digest,'Frozen runtime support changed')

def parent_check(phase,digest):
    path=ROOT/(phase+'-launch/summary.json');require(sha(path)==digest,phase+' receipt changed')
    p=json.loads(path.read_text());expected=plans(phase)
    require(p['status']=='PASS' and p['namespace_exit']==0 and not p['timed_out'] and not p['cleanup_actions'] and not p['own_residual_children'] and p['original_configuration_preserved'],'Phase lifecycle failed: '+phase)
    require([(g['index'],g['variant'],g['round']) for g in p['groups']]==[(g['index'],g['variant'],g['round']) for g in expected],'Variant/round order changed')
    require(p['variant_group_counts']==dict.fromkeys(VARIANTS,3 if phase=='correctness' else 10),'Variant phase group counts')
    require(sha(ROOT/phase/'summary.json')==p['worker_summary_sha256'],'Phase worker changed')
    require(sha(ROOT/phase/'schedule-plan.json')==p['schedule_plan_sha256'] and sha(ROOT/phase/'schedule-actual.json')==p['schedule_actual_sha256'],'Schedule receipt changed')
    actual=json.loads((ROOT/phase/'schedule-actual.json').read_text())
    require([(g['index'],g['variant'],g['round']) for g in actual]==[(g['index'],g['variant'],g['round']) for g in expected],'Actual schedule incomplete')
    for group,plan,order in zip(p['groups'],expected,actual):
        require(all(group.get(k)==v for k,v in plan.items()),'Group schedule mismatch')
        directory=ROOT/phase/('g'+str(group['index']).zfill(3))
        require(group['status']=='PASS' and sha(directory/'summary.json')==group['summary_sha256'],'Saved group changed')
        require(sha(directory/'analysis.json')==group['analysis_sha256'],'Saved group analysis changed')
        row=json.loads((directory/'summary.json').read_text())
        for field in ('started_utc','finished_utc','started_monotonic_ns','finished_monotonic_ns','elapsed_seconds','gap_since_previous_group_seconds'):
            require(field in row and row[field]==order[field],'Actual timing receipt changed')
        require(row['finished_monotonic_ns']>=row['started_monotonic_ns'],'Negative pair duration')
    return p,actual

def summarize(captures,metadata):
    """Pure CPU aggregation, retaining rank/core/round identities and full precision."""
    summaries={}
    for variant in VARIANTS:
        chosen=[x for x in captures if x['variant']==variant]
        require(len(chosen)==20,'Expected twenty rank captures per variant')
        by_rank={};us=[];workload=[];batches=[]
        for rank in (0,1):
            selected=[x for x in chosen if x['rank']==rank]
            require([x['round'] for x in selected]==list(range(1,11)),'Incomplete ten fresh rounds')
            require(all(set(x['metrics'])==set(selected[0]['metrics']) for x in selected),'Metric coverage changed')
            by_rank[str(rank)]={metric:aggregate([(x['round'],rank,x['metrics'][metric]) for x in selected]) for metric in selected[0]['metrics']}
            us.append(dict(rank=rank,us={name:distribution([x['four_us'][i] for x in selected]) for i,name in enumerate(FOUR_US)}))
            for cohort,cores in (('receiver',range(4)),('non_receiver',range(4,64))):
                totals={key:[sum(x['capture']['lanes'][c]['workload'][key] for c in cores) for x in selected] for key in WORK}
                workload.append(dict(rank=rank,cohort=cohort,cores=len(cores),denominator='cohort workload sum per rank/capture; equal ten rounds',per_round={key:distribution(v) for key,v in totals.items()}))
            capacity=metadata[variant]['capacity']
            require(isinstance(capacity,int) and capacity>0,'Invalid batch capacity')
            for x in selected:
                per_core={str(c):math.ceil(x['capture']['lanes'][c]['workload']['received_records']/capacity) for c in range(4)}
                actual_records=sum(x['capture']['lanes'][c]['workload']['received_records'] for c in range(4))
                batches.append(dict(round=x['round'],rank=rank,capacity=capacity,records_per_receiver=[x['capture']['lanes'][c]['workload']['received_records'] for c in range(4)],successful_batches_per_core_derived=per_core,successful_batches_per_rank_derived=sum(per_core.values()),payload_feature_bytes_per_rank_derived=actual_records*512,dma_read_bytes_per_rank_derived=actual_records*(544 if variant.startswith('R') else 960),received_wire_bytes_actual=sum(x['capture']['lanes'][c]['workload']['received_wire_bytes'] for c in range(4)),scope='ceil(actual received_records/capacity); feature512B, DMA960B B/U or544B R64/R128 per record; derived, not additional hardware counter'))
        overall={metric:aggregate([(x['round'],x['rank'],x['metrics'][metric]) for x in chosen]) for metric in chosen[0]['metrics']}
        round_max=[]
        for number in range(1,11):
            pair=[x for x in chosen if x['round']==number];require(len(pair)==2,'Missing paired rank')
            maxima=[max(x['four_us'][i] for x in pair) for i in range(4)]
            ties=[[x['rank'] for x in pair if x['four_us'][i]==value] for i,value in enumerate(maxima)]
            round_max.append([number,*maxima,ties])
        external_slowest={name:[dict(round=x['round'],rank=x['rank'],us=x['four_us'][i]) for x in chosen if x['four_us'][i]==max(row['four_us'][i] for row in chosen)] for i,name in enumerate(FOUR_US)}
        summaries[variant]=dict(metadata=metadata[variant],statistics_by_rank=by_rank,statistics_equal_rank_round=overall,rank_us=us,round_rank_max_four_us=round_max,rank_max_four_us={name:distribution([x[i+1] for x in round_max]) for i,name in enumerate(FOUR_US)},four_us_real_slowest=external_slowest,workload=workload,batches=batches)
    return summaries

def compact_delivery(build,summaries,archive,manifest,sources,parent):
    files={name:dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)) for name in ('four_us.csv','per_round_stages.csv','per_round_slowest.csv','per_core_workload.csv','all-statistics.json','numeric.json','actual-order.json','workload-summary.json','trace-local-spec-1GHz.json') for p in (OUT/name,)}
    per_variant={}
    for variant,summary in summaries.items():
        rows=[]
        for metric in PRIMARY:
            item=summary['statistics_equal_rank_round'][metric]
            rows.append([metric,item['participants'],item['mean_ticks'],item['max_ticks']])
        p=OUT/('summary-'+variant+'.json')
        per_variant[variant]=dict(capacity=summary['metadata']['capacity'],ub_bytes=summary['metadata']['ub_bytes'],correctness_pairs=3,capture_pairs=10,rank_captures=20,primary_metric_rows=rows,four_us_rank_max=[[summary['rank_max_four_us'][name][key] for key in ('mean','median','max')] for name in FOUR_US],summary=dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
    result=dict(schema='tilelang_targeted_p2_107_delivery.v1',status='PASS',read_only_existing_evidence=True,npu_launch=False,device_queries=False,candidate=build['candidate'],experiment_revision=REVISION,target='141.61.73.107',devices=[0,1],cores=[64,64],case=dict(world_size=2,experts=4,tokens_per_rank=256,hidden=256,topk=2,dtype='BF16',routing='balanced'),variants=per_variant,correctness_groups=12,capture_groups=40,rank_captures_reanalyzed=80,sdk_record_parses=80,saved_analysis_json_normalized_equal=True,original_configuration_preserved=True,clock='NOT_VERIFIED',metric_columns=['metric','participants_per_rank','mean_ticks_equal_rank_round','real_max_ticks'],four_us_order=list(FOUR_US),four_us_columns=['mean','median','max'],units='Raw ticks retained; internal us=ticks/1000 per 950DT 1GHz specification, not measured calibration; ACL/msprof already us',mean_scope='Equal ten rounds and two ranks; actual participant core denominator; receiver core0-3=4/non_receiver core4-63=60; same-core sums before mean/max',boundary_note='F8 includes final output drain; F9 algorithm unchanged, capacity-dependent work reported; F8+F9 sum on identical receiver core/capture before true max',pre_dispatch_scope='AllReduce plus event boundaries; not pure HCCL kernel duration',tails_scope='Each external metric takes paired rank maximum independently, not globally aligned interval',sources=dict(build_summary_sha256=BUILD_SHA,correctness_parent_sha256=CORRECTNESS_SHA,capture_parent_sha256=PARENT_SHA,capture_worker_sha256=parent['worker_summary_sha256'],manifest_path=str(manifest),manifest_sha256=sha(manifest),captures_path=str(sources),captures_sha256=sha(sources)),archive=archive,files=files)
    text=json.dumps(result,ensure_ascii=True,separators=(',',':'));require(len(text.encode())<14000,'Compact output exceeds budget; use saved files, never replay capture')
    return result

def main():
    require(all(len(value)==64 and all(c in '0123456789abcdef' for c in value) for value in (BUILD_SHA,CORRECTNESS_SHA,PARENT_SHA)),'Build/correctness/capture receipt SHA must be pinned')
    integrity();build=json.loads((ROOT/'build-summary.json').read_text());source_check(build)
    correctness,correct_order=parent_check('correctness',CORRECTNESS_SHA);parent,cap_order=parent_check('capture',PARENT_SHA)
    require(not OUT.exists(),'Saved export exists; audit/read it instead of replaying');OUT.mkdir()
    sys.path.insert(0,str(ROOT/'source/examples'))
    from tilelang_dispatch.analyze_native import analyze
    captures=[];sources=[];us_rows=[];stage_rows=[];slow_rows=[];workload_rows=[];trace=[];capture_ids=set()
    metadata={v['name']:v for v in build['variant_metadata']}
    normalize=lambda x:json.loads(json.dumps(x))
    for group in parent['groups']:
        variant=group['variant'];number=group['round'];directory=ROOT/'capture'/('g'+str(group['index']).zfill(3))
        require(sha(directory/'analysis.json')==group['analysis_sha256'] and sha(directory/'capture-evidence.json')==group['capture_evidence_sha256'],'Accepted evidence changed')
        for name,digest in json.loads((directory/'capture-evidence.json').read_text()).items():require(sha(directory/name)==digest,'Capture evidence file changed')
        saved=json.loads((directory/'analysis.json').read_text());fresh=analyze(directory/'native',ROOT/'fixture',ROOT/'generated'/variant)
        require(normalize(fresh)==saved,'SDK reanalysis differs from accepted analysis')
        require(fresh['status']=='PASS' and fresh['rounds']==1 and fresh['warmup']==20,'SDK reanalysis acceptance')
        for rank in (0,1):
            result=fresh['rank_results'][rank];require(result['rank']==rank and result['cores']==64 and len(result['captures'])==1,'Rank identity')
            cap=result['captures'][0]
            require(cap['capture_id'] not in capture_ids and cap['operator_correctness']=='PASS' and cap['capture_structure']=='PASS' and cap['performance_sample_accepted'] and cap['unit']=='ticks' and cap['cycles_per_us'] is None,'Capture identity/acceptance')
            capture_ids.add(cap['capture_id']);metrics=capture_statistics(cap)
            rank_root=directory/'native/round1'/('rank'+str(rank))
            sources.append(dict(variant=variant,round=number,rank=rank,ordinal=group['ordinal'],capture_id=cap['capture_id'],records=str((rank_root/'round20.dispatch.records').relative_to(ROOT)),records_sha256=sha(rank_root/'round20.dispatch.records'),analysis_sha256=group['analysis_sha256'],evidence_sha256=group['capture_evidence_sha256']))
            pair=fresh['pairs'][0]['ranks'][rank]
            require(pair['rank']==rank and pair['pre_allreduce']['sum_results_checked']==22,'Pre-AllReduce scope')
            timing=[pair['acl_event_us'],pair['device_task_us'],pair['pre_allreduce']['pre_dispatch_event_us'],pair['pre_allreduce']['segment_event_us']]
            require(all(math.isfinite(x) and x>0 for x in timing),'Timing evidence');us_rows.append([variant,number,rank,group['ordinal'],*timing])
            for metric,item in metrics.items():
                stage_rows.append([variant,number,rank,metric,item['status'],item['participants'],item['mean_ticks'],item['max_ticks']])
                if item['participants']:slow_rows.append([variant,number,rank,metric,item['max_ticks'],item['max_ticks']/1000,json.dumps(item['slowest_cores'],separators=(',',':'))])
            for lane in cap['lanes']:
                core=lane['core'];workload_rows.append([variant,number,rank,core,*[lane['workload'][key] for key in WORK]])
                pid=group['index']*128+rank*64+core+1;origin=lane['intervals']['module.whole']['begin_tick']
                trace.append(dict(ph='M',name='process_name',pid=pid,tid=0,args=dict(name='%s/round%d/rank%d/core%d independent t0'%(variant,number,rank,core))))
                for name,interval in lane['intervals'].items():trace.append(dict(ph='X',name=name,cat='local-core-spec-1GHz',pid=pid,tid=0,ts=(interval['begin_tick']-origin)/1000,dur=interval['ticks']/1000,args=dict(variant=variant,round=number,rank=rank,core=core,capture_id=cap['capture_id'],raw_start=interval['begin_tick'],raw_end=interval['end_tick'],clock='NOT_VERIFIED')))
            captures.append(dict(variant=variant,round=number,rank=rank,metrics=metrics,capture=cap,four_us=timing))
    require(len(captures)==len(capture_ids)==80,'Eighty unique rank captures required')
    summaries=summarize(captures,metadata)
    csv_file('four_us.csv',['variant','round','rank','schedule_ordinal',*FOUR_US],us_rows)
    csv_file('per_round_stages.csv',['variant','round','rank','metric','status','participants','mean_ticks','max_ticks'],stage_rows)
    csv_file('per_round_slowest.csv',['variant','round','rank','metric','max_ticks','max_us_spec_1GHz','cores_json'],slow_rows)
    csv_file('per_core_workload.csv',['variant','round','rank','core',*WORK],workload_rows)
    save(OUT/'capture-sources.json',sources);save(OUT/'all-statistics.json',summaries)
    save(OUT/'workload-summary.json',{v:dict(workload=s['workload'],batches=s['batches']) for v,s in summaries.items()})
    save(OUT/'actual-order.json',dict(correctness=correct_order,capture=cap_order,scope='Real UTC/monotonic pair start/end and inter-pair gaps; not global device clock alignment'))
    save(OUT/'numeric.json',dict(captures=captures,summaries=summaries))
    save(OUT/'trace-local-spec-1GHz.json',dict(traceEvents=trace,clock='NOT_VERIFIED',scope='All core lanes independently rebased to zero; no cross-core/rank ordering',units='us from ticks/1000 per 950DT 1GHz specification; not measured calibration'))
    for variant,summary in summaries.items():save(OUT/('summary-'+variant+'.json'),summary)
    (OUT/'交付说明.md').write_text('四组B/U/R64/R128独立构建，12fresh正确性后40fresh循环轮换交错采集；P2/E4/T256/H256/K2/BF16 balanced，107物理0/1各64核，同12业务SHA/AR SUM2，112B Host及52槽。仅SDK重解析既有80份rank capture，无设备查询、ACL初始化或NPU重跑。完整source/generated/install/build-{variant}/fixture/正确性/采集/实际顺序与间隔及SHA留包。各阶段先同核组合再按真实参与核统计；⑤/F7/F9接收4核和非接收60核分列，未执行为NOT_EXECUTED。F8+F9同核同轮求和后取真实max，所有最慢rank/core/round及并列保存。F8 drain不移动，F9批容量影响单列；批次数由实际每核records和容量推导而非新增计数。ticks保留，内部us=ticks/1000仅规格换算，clock NOT_VERIFIED；trace每核独立归零。四类外部us逐rank/逐轮及每轮慢rankmean/median/max独立保存。无任何性能数值占位或伪造。\n')
    # Verify frozen source again before sealing the archive; extraction only writes its own OUT.
    integrity();source_check(build)
    files={};directories=['source','generated','fixture','install','core-build','core-install','correctness','correctness-launch','correctness-entry01','capture','capture-launch','capture-entry01','validated-export01','deps/backend/src/tl_templates']+['build-'+v for v in VARIANTS]
    for relative in directories:
        directory=ROOT/relative;require(directory.is_dir(),'Archive input absent: '+relative)
        for path in directory.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and '.git' not in path.parts and path.suffix!='.pyc':
                require(path.resolve().is_relative_to(ROOT),'Archive path escape');files[str(path.relative_to(ROOT))]=path
    for path in ROOT.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.py','.cpp','.zip'):files[str(path.relative_to(ROOT))]=path
    for relative in ('export-support01','runtime-support01'):
        directory=ROOT/relative
        if directory.is_dir():
            for path in directory.rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':require(path.resolve().is_relative_to(ROOT),'Support path escape');files[str(path.relative_to(ROOT))]=path
    require(len(files)<50000 and sum(p.stat().st_size for p in files.values())<3*1024**3,'Archive bounds')
    manifest=OUT/'evidence-manifest.json';save(manifest,dict(schema='tilelang_targeted_p2_107_manifest.v1',candidate=build['candidate'],experiment_revision=REVISION,files={n:sha(p) for n,p in sorted(files.items())},extraction_script_sha256=sha(Path(__file__))))
    files[str(manifest.relative_to(ROOT))]=manifest
    archive=ROOT/'P2-F7F8-B-U-R64-R128-e788bc72-validated-evidence.zip';require(not archive.exists(),'Archive already exists; inspect saved evidence')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,path in sorted(files.items()):z.write(path,name)
    with zipfile.ZipFile(archive) as z:
        require(z.testzip() is None and len(z.namelist())==len(files) and set(z.namelist())==set(files),'ZIP CRC/membership')
        for name,path in files.items():require(hashlib.sha256(z.read(name)).hexdigest()==sha(path),'ZIP member SHA')
    sealed=dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive),members=len(files),crc_and_all_member_sha_verified=True)
    delivery=compact_delivery(build,summaries,sealed,manifest,OUT/'capture-sources.json',parent)
    save(OUT/'delivery.json',delivery);print(json.dumps(delivery,ensure_ascii=True,separators=(',',':')));return 0
if __name__=='__main__':raise SystemExit(main())
