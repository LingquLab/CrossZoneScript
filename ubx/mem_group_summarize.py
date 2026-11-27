#!/usr/bin/env python3
"""汇总单机16rank本地完成时长；不跨卡相减绝对设备时间戳。"""
import argparse,csv,json,statistics
from collections import defaultdict
from pathlib import Path

def stats(values):
 values=sorted(values);position=(len(values)-1)*.95;lo=int(position);hi=min(lo+1,len(values)-1)
 return dict(count=len(values),mean=statistics.mean(values),p95=values[lo]+(values[hi]-values[lo])*(position-lo),maximum=max(values))
def csvfile(path,rows):
 with path.open('w',newline='',encoding='utf-8-sig') as stream:
  writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def main():
 p=argparse.ArgumentParser();p.add_argument('--input',action='append',required=True,help='host=run-directory; may repeat for detail data');p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
 allrows=[];cores=[];groups=defaultdict(list);configurations=[]
 for spec in a.input:
  host,directory=spec.split('=',1);directory=Path(directory);config=json.loads((directory/'config.json').read_text());configurations.append(dict(host=host,**config));result=json.loads((directory/'result.json').read_text());assert result['status']=='PASS'
  for rank in range(16):
   for line in (directory/('rank%d.jsonl'%rank)).read_text().splitlines():
    r=json.loads(line);assert r['verified'] and r['rank']==rank
    base={k:v for k,v in r.items() if k not in ['profiles','category_records']};base.update(host=host,run=directory.name,self_records=r['category_records'][0],same_group_peer_records=r['category_records'][1],cross_group_records=r['category_records'][2]);allrows.append(base)
    for core in r['profiles']:
     cores.append(dict(host=host,run=directory.name,test=r['test'],repeat=r['repeat'],round=r['round'],warmup=r['warmup'],detail=r['detail'],rank=rank,role='send' if core['core']<r['send_cores'] else 'observe',**core,duration_us=(core['end']-core['start'])/1000,lane_wait_upper_bound_us=core['wait_ticks']/1000))
    if not r['warmup']:groups[(host,directory.name,r['detail'],r['repeat'],r['test'],r['round'])].append(r)
 rounds=[]
 for (host,run,detail,repeat,test,roundno),rows in sorted(groups.items()):
  assert len(rows)==16 and len({r['rank'] for r in rows})==16
  count=[sum(r['category_records'][i] for r in rows) for i in range(3)];receive=max(r['receive_us'] for r in rows);sent=sum(r['records_sent'] for r in rows);assert sent==sum(r['records_received'] for r in rows)==sum(count)
  send_profiles=[c for r in rows for c in r['profiles'][:r['send_cores']]]
  rounds.append(dict(host=host,run=run,detail=detail,repeat=repeat,test=test,round=roundno,send_cores=rows[0]['send_cores'],slowest_rank_receive_us=receive,slowest_rank_send_us=max(r['send_us'] for r in rows),self_records=count[0],same_group_peer_records=count[1],cross_group_records=count[2],cross_card_wire_bytes=(count[1]+count[2])*4096,cross_card_payload_bytes=(count[1]+count[2])*3584,observed_wire_GBps=(count[1]+count[2])*4096/receive/1000 if receive else 0,observed_payload_GBps=(count[1]+count[2])*3584/receive/1000 if receive else 0,send_aiv_mean_us=statistics.mean((c['end']-c['start'])/1000 for c in send_profiles),send_aiv_max_us=max((c['end']-c['start'])/1000 for c in send_profiles),lane_wait_upper_bound_sum_us=sum(c['wait_ticks'] for c in send_profiles)/1000))
 summaries=[]
 bytest=defaultdict(list)
 for row in rounds:bytest[(row['host'],row['detail'],row['test'])].append(row)
 for (host,detail,test),rows in sorted(bytest.items()):
  summary=dict(host=host,detail=detail,test=test,send_cores=rows[0]['send_cores'])
  for metric in ['slowest_rank_receive_us','slowest_rank_send_us','send_aiv_mean_us','send_aiv_max_us','observed_wire_GBps','observed_payload_GBps','lane_wait_upper_bound_sum_us']:
   summary.update({metric+'_'+key:value for key,value in stats([r[metric] for r in rows]).items()})
  for key in ['self_records','same_group_peer_records','cross_group_records','cross_card_wire_bytes']:
   values={r[key] for r in rows};assert len(values)==1;summary[key]=values.pop()
  summaries.append(summary)
 for host in {r['host'] for r in rounds}:
  table={r['test']:r for r in summaries if r['host']==host and r['detail']==0}
  if len(table)==10:
   for baseline,intra,inter,cross_first,local_first in [('T01','T03','T05','T07','T09'),('T02','T04','T06','T08','T10')]:
    for key in ['self_records','same_group_peer_records','cross_group_records']:
     assert table[baseline][key]==table[intra][key]+table[inter][key]
     assert table[baseline][key]==table[cross_first][key]==table[local_first][key]
 csvfile(a.output/'all_rank_rounds.csv',allrows);csvfile(a.output/'core_profiles.csv',cores);csvfile(a.output/'per_round.csv',rounds);csvfile(a.output/'summary.csv',summaries)
 (a.output/'configurations.json').write_text(json.dumps(configurations,indent=2));(a.output/'summary.json').write_text(json.dumps(summaries,indent=2))
 print(json.dumps(dict(status='PASS',rank_rounds=len(allrows),measured_server_rounds=len(rounds),summary=[{k:r[k] for k in ['host','detail','test','slowest_rank_receive_us_mean','slowest_rank_receive_us_p95','slowest_rank_receive_us_maximum','cross_card_wire_bytes']} for r in summaries])))
if __name__=='__main__':main()
