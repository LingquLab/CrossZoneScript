"""Read only two SHA-bound saved statistics files; no device APIs or telemetry."""
from pathlib import Path
import csv, hashlib, io, json, math, sys

contract=json.loads(sys.argv[1])
root=Path(contract['source_root'])
loaded={}
for name, expected in contract['files'].items():
    path=root/name
    raw=path.read_bytes()
    assert len(raw)==expected['bytes'] and hashlib.sha256(raw).hexdigest()==expected['sha256'],name
    loaded[name]=raw
stats=json.loads(loaded['all-statistics.json'])
variants=['B','U','R64','R128']
metrics=[row[0] for row in contract['primary_metric_rows']['B']]
metrics += ['functions.F%d.all'%i for i in range(7)]
metrics += ['stages.F7.'+name+'.'+cohort for name in ('layout_wait','gate_clear','receive_plan') for cohort in ('receiver','non_receiver')]
metrics += ['stages.F8.payload_loop.receiver','functions.F9.non_receiver','stages.F9.payload_clear.receiver','stages.F9.final_fence.receiver','stages.F9.final_fence.non_receiver']
assert len(metrics)==len(set(metrics))
rows=[];batch_rows=[]
for variant in variants:
    summary=stats[variant];overall=summary['statistics_equal_rank_round']
    for name, participants, mean, maximum in contract['primary_metric_rows'][variant]:
        item=overall[name]
        assert item['participants']==participants and math.isclose(item['mean_ticks'],mean,rel_tol=0,abs_tol=1e-8) and item['max_ticks']==maximum
    packed=[]
    for metric in metrics:
        item=overall[metric]
        if item['participants']==0:
            assert item['mean_ticks'] is None and item['max_ticks'] is None
            packed.append([0,None,None,None,0]);continue
        assert item['slowest']
        first=item['slowest'][0]
        mask=sum(1<<c for c in first['cores'])
        packed.append([item['participants'],item['mean_ticks'],item['max_ticks'],[first['round'],first['rank'],format(mask,'x')],len(item['slowest'])])
    rows.append(packed)
    batches=summary['batches']
    assert len(batches)==20 and all(row['records_per_receiver']==[128]*4 for row in batches)
    assert len({row['successful_batches_per_rank_derived'] for row in batches})==1
    batch_rows.append([summary['metadata']['capacity'],batches[0]['successful_batches_per_rank_derived'],batches[0]['payload_feature_bytes_per_rank_derived'],batches[0]['dma_read_bytes_per_rank_derived'],batches[0]['received_wire_bytes_actual']])
csv_rows=list(csv.DictReader(io.StringIO(loaded['four_us.csv'].decode())))
assert len(csv_rows)==80
us_names=['dispatch_acl_us','msprof_dispatch_us','pre_dispatch_event_us','segment_event_us']
us_rows=[]
for row in csv_rows:
    us_rows.append([variants.index(row['variant']),int(row['round']),int(row['rank']),int(row['schedule_ordinal']),*[round(float(row[k]),3) for k in us_names]])
assert len({tuple(row[:3]) for row in us_rows})==80
result=dict(schema='tilelang_targeted_p2_compact_details.v1',status='PASS',read_only=True,npu_launch=False,device_queries=False,candidate=contract['candidate'],variants=variants,metrics=metrics,metric_row_columns=['participants','mean_ticks','real_max_ticks','one_real_slowest_round_rank_core_mask_hex','tied_round_rank_entries_total'],metric_rows=rows,slowest_scope='One genuine tied entry shown per metric; its mask includes all tied cores in that round/rank. Every tied identity is retained in SHA-bound all-statistics.json, not truncated there.',four_us_order=us_names,four_us_row_columns=['variant_index','round','rank','schedule_ordinal',*us_names],four_us_rows=us_rows,four_us_precision='Rounded to 3 decimal places for compact display; original full-precision CSV remains SHA-bound.',batch_columns=['capacity','successful_batches_per_rank_derived','feature_bytes_per_rank','dma_read_bytes_per_rank','wire_bytes_per_rank'],batch_rows=batch_rows,batch_scope='Derived from actual received_records, not an extra hardware counter; all twenty rank captures per variant have identical displayed workload.',clock='NOT_VERIFIED',source_files=contract['files'])
text=json.dumps(result,ensure_ascii=True,separators=(',',':'))
assert len(text.encode())<14000,len(text.encode())
print(text)
