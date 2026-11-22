"""Export the recorded EP32 events as native Perfetto TrackEvent packets, entirely offline."""
from collections import defaultdict
from pathlib import Path
import csv
import hashlib
import io
import json
import os
import shutil
import ssl
import subprocess
import urllib.request

HASHES = ['5af7410a027e4e00c1248f8a726f3a977d4226502de3dfb5c7237522f8e3d8d1',
          '8014b288ead213e766d41bfa189b8c9102511fd41c7d710c1751485f9a911e8a']

def varint(n):
    v=bytearray()
    while n>=128:
        v.append((n & 127)|128)
        n >>= 7
    v.append(n)
    return bytes(v)

def num(k,n):
    return varint(k*8)+varint(n)

def blob(k,v):
    if isinstance(v,str): v=v.encode()
    return varint(k*8+2)+varint(len(v))+v

def packet(f,data):
    f.write(blob(1,data))

def native(path, rows, names, overview):
    # Split overlapping complete intervals on parallel lanes instead of inventing nesting.
    grouped=defaultdict(list)
    for rank,track,name,us,dur,args in rows:
        grouped[rank,track].append((round(us*1000),round((us+dur)*1000),name,args))
    origin=min(a for group in grouped.values() for a,*_ in group)
    total=0
    with path.open('wb') as f:
        for rank in sorted({r for r,t in grouped}):
            pid=rank+1
            process='EP32 Combine overview' if overview else f'EP32 rank {rank:02}'
            packet(f,blob(60,num(1,pid)+blob(3,num(1,pid)+blob(6,process))))
        tid=10000
        for key,group in sorted(grouped.items()):
            rank,track=key
            lanes=[];ends=[]
            for a,b,name,args in sorted(group,key=lambda e:(e[0],-e[1])):
                ix=0
                if b>a:
                    ix=next((j for j,v in enumerate(ends) if v<=a),len(ends))
                if ix==len(lanes): lanes.append([]);ends.append(a)
                if b>a: ends[ix]=b
                lanes[ix].append((a,1 if b==a else 2,name,args))
                if b>a: lanes[ix].append((b,0,'',{}))
                total+=1
            for ix,events in enumerate(lanes):
                tid+=1;uuid=tid+100000
                title=names.get(key,str(track))+(f' / overlap {ix}' if ix else '')
                desc=num(1,uuid)+blob(2,title)+blob(4,num(1,rank+1)+num(2,tid)+blob(5,title))
                packet(f,blob(60,desc))
                for stamp,order,name,args in sorted(events,key=lambda e:(e[0],e[1])):
                    event=num(9,{0:2,1:3,2:1}[order])+num(11,uuid)
                    if order:
                        event+=blob(23,name)
                        for k,v in args.items():
                            val=num(3,v) if type(v)==int and 0<=v<2**64 else blob(6,str(v))
                            event+=blob(4,blob(10,k)+val)
                    packet(f,num(8,stamp-origin+1)+num(10,1)+blob(11,event))
    return total

def convert(data,dest,i):
    groups=defaultdict(lambda: defaultdict(list));names={};detail=[]
    for e in data['traceEvents']:
        r=e['pid'];t=e.get('tid',0);ph=e['ph'];args=e.get('args',{})
        if ph=='M' and e['name']=='thread_name': names[r,t]=args['name']
        if ph in ('i','X'):
            detail.append((r,t,e['name'],e['ts'],e.get('dur',0),args))
            if ph=='i' and args.get('eventID') in (2034,2037,2040):groups[r][args['eventID']].append(e['ts'])
    assert set(groups)==set(range(32))
    overview=[];labels={}
    for r,g in sorted(groups.items()):
        assert len(g[2034])==len(g[2037])==56 and len(g[2040])==1
        a,b,c=min(g[2034]),max(g[2037]),g[2040][0]
        assert a<b<=c
        labels[0,r]=f'rank {r:02}'
        overview.extend([(0,r,'Combine data',a,b-a,{'rank':r}), (0,r,'Complete / reuse',b,c-b,{'rank':r})])
    results=[]
    for kind,rows,tracks,small in [('overview',overview,labels,True),('detail',detail,names,False)]:
        path=dest/f'round-{i}-{kind}.pftrace'
        temp=path.with_suffix('.tmp')
        count=native(temp,rows,tracks,small)
        os.replace(temp,path)
        results.append((path,count))
    return results

def check(exe,path):
    query='SELECT count(*) AS slices, count(distinct track_id) AS tracks, count(distinct upid) AS processes, sum(dur>0) AS spans, round((max(ts+dur)-min(ts))/1000.0,3) AS span_us FROM slice JOIN thread_track ON thread_track.id=slice.track_id JOIN thread USING(utid)'
    r=subprocess.run([str(exe),'query','--format','csv',str(path),query],capture_output=True,timeout=200)
    if r.returncode: return {'exit':r.returncode,'error':r.stderr.decode(errors='replace')[-350:]}
    return next(csv.DictReader(io.StringIO(r.stdout.decode('utf-8-sig'))))

def main():
    src=Path.cwd()/'trace-mixed-75076b89-003'
    dest=Path(r'D:\project\UBX攻关\EP32-Combine-75076b89-Perfetto')
    dest.mkdir(exist_ok=True)
    result=[]
    for i,sha in enumerate(HASHES):
        old=src/f'round-{i}.perfetto.json'
        raw=old.read_bytes();assert hashlib.sha256(raw).hexdigest()==sha
        m=json.loads(raw);del raw
        assert m['complete'] and m['round']==i and m['ranks']==32
        result.extend(convert(m,dest,i));del m
    summary={'directory':str(dest),'files':[{'name':p.name,'events':n,'bytes':p.stat().st_size} for p,n in result]}
    print(json.dumps(summary),flush=True)
    tool=shutil.which('trace_processor_shell')
    if not tool:
        tool=Path.cwd()/'perfetto-v59.1-trace_processor_shell.exe'
        expected='3acc5aca1c764541033a82ba3bb0a47524b5c3c0aa194eea8d9a7b0cdb346bdc'
        if not tool.is_file():
            url='https://commondatastorage.googleapis.com/perfetto-luci-artifacts/v59.1/windows-amd64/trace_processor_shell.exe'
            with urllib.request.urlopen(url,timeout=18,context=ssl._create_unverified_context()) as response:
                binary=response.read(15872001)
            assert hashlib.sha256(binary).hexdigest()==expected
            tool.write_bytes(binary)
        assert hashlib.sha256(tool.read_bytes()).hexdigest()==expected
    summary['original_import']=[check(tool,src/f'round-{i}.perfetto.json') for i in range(2)]
    for info,(path,n) in zip(summary['files'],result):
        stats=check(tool,path);info['import']=stats
        assert int(stats['slices'])==n and int(stats['spans'])>0
        assert int(stats['tracks'])>=32 and int(stats['processes'])==(1 if 'overview' in path.name else 32)
    (dest/'import-check.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary))

if __name__=='__main__':main()
