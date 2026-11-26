# Copyright (c) 2026 TileXR Project.
"""Generate deterministic expert routes and local HT SST inputs from the candidate ABI."""
import argparse
from array import array
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import shutil

GUARD=struct.pack('<f',-17.)


class DiskImages:
    """Keep only paths between ranks; the immutable loader reads one image at a time."""
    def __init__(self,directory,images):
        directory.mkdir(parents=True,exist_ok=False)
        self.rows=[]
        for image in images:
            name=image['name'];assert Path(name).name==name and '/' not in name and '\\' not in name
            path=directory/(name+'.bin');path.write_bytes(image['data'])
            self.rows.append((name,image['addr'],path,len(image['data'])))

    def __iter__(self):
        for name,address,path,size in self.rows:
            payload=path.read_bytes();assert len(payload)==size
            yield dict(name=name,addr=address,data=payload)

def prepare(source, output, world, tokens, kernel,disk_backed=False, revision=None, intra_cores=8, peer_slots=1):
    source,output,kernel=map(Path,(source,output,kernel))
    output.mkdir(parents=True,exist_ok=False)
    support=source/'tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0,str(support))
    import loader,runtime
    helper=output/'prefill-tool'
    common=['c++','-std=c++14','-O2','-DTILEXR_COMBINE_HT_SST_FP32=1','-DTILEXR_COMBINE_HT_SIM_SEND=1','-I'+str(source/'include')]
    subprocess.run(common+['-I'+str(source/'src/moonep/combine_ht/common'),str(Path(__file__).with_name('prefill.cpp')),
                          str(source/'src/moonep/combine_ht/host/combine_ht_prefill.cpp'),
                          str(source/'src/moonep/combine_ht/host/combine_ht_pipeline_layout.cpp'),'-o',str(helper)],check=True)
    probe=output/'abi-probe'
    subprocess.run(common+[str(support/'abi_probe.cpp'),'-o',str(probe)],check=True)
    hidden,topk,experts=128,6,256
    capacity=tokens*topk
    rows=world*capacity
    chunks = min(tokens, 4)
    assert tokens % chunks == 0
    chunk_tokens = tokens // chunks
    owner_cores = 16
    assert intra_cores in (4, 8) and peer_slots in (1, 2)
    batch_width = owner_cores * peer_slots
    groups = (world - 8 + batch_width - 1) // batch_width
    rng=random.Random(20261124)
    routes=[]
    ledger={}
    for s in range(world):
        for t in range(tokens):
            selected=rng.sample(range(experts),topk)
            routes.append({'source':s,'token':t,'experts':selected})
            for slot,e in enumerate(selected):
                rank=e//(experts//world)
                row=(s*tokens+t)*topk+slot
                value=struct.pack('<128f',*[1+(s*17+t*3+e*5+slot*11+h%13)/256 for h in range(hidden)])
                ledger[row]=(rank,value)
    (output/'routes.json').write_text(json.dumps(routes,separators=(',',':')))
    stats={'world':world,'tokens':tokens,'hidden':hidden,'topk':topk,'experts':experts,'seed':20261124,
           'dtype':'fp32','contributions':len(ledger),'chunks':chunks,'groups':groups,
           'intra_cores':intra_cores,'peer_slots':peer_slots,'send_rows':0,'direct_rows':0,'reduce_rows':0}
    assert len(ledger)==world*tokens*topk
    specs=[]
    for rank in range(world):
        base=rank*runtime.GM_BYTES
        name='rank%02d'%rank
        meta=output/'metadata'/name
        meta.parent.mkdir(exist_ok=True)
        abi=json.loads(subprocess.check_output([str(probe),str(rank),str(tokens),'128','6',str(rows),str(rows),'2026','-',str(world),'8',str(chunk_tokens),'1',str(owner_cores),str(peer_slots),'1']))
        images=runtime.runtime_images(abi,rank,1,world=world)
        fwd=array('i',[0])*(world*capacity*6)
        counts=[0]*world
        ranges=[0]*(world*chunks*2)
        masks=[0]*(chunks*groups)
        backward=[[] for _ in range(8)]
        sends=[[[] for _ in range(chunks)] for _ in range(8)]
        inp=bytearray(GUARD*(rows*hidden))
        gathered=bytearray(inp)
        partial=bytearray(inp)
        server=rank//8
        for row,(owner,value) in ledger.items():
            if owner==rank: inp[row*512:(row+1)*512]=value
        for chunk in range(chunks):
            for s in range(world):
                ranges[(s*chunks+chunk)*2]=counts[s]
                group = groups - 1 if s // 8 == server else (s - 8 * (s // 8 > server)) // batch_width
                for t in range(tokens):
                    if t*chunks//tokens!=chunk: continue
                    route=routes[s*tokens+t]
                    ids=[(slot,e) for slot,e in enumerate(route['experts']) if e//(experts//world)//8==server]
                    if not ids: continue
                    first,e=ids[0]
                    gateway=e//(experts//world)
                    primary=(s*tokens+t)*topk+first
                    start=counts[s]
                    if gateway==rank:
                        records=[(-1,0,-1)] if len(ids)==1 else []
                        for slot,e in ids[1:]:
                            owner=e//(experts//world)
                            row=(s*tokens+t)*topk+slot
                            records.append((owner,row,-1 if owner==rank else row))
                            if owner!=rank:
                                masks[group * chunks + chunk] |= 1 << (owner % 8)
                                gathered[row*512:(row+1)*512]=ledger[row][1]
                        for owner,row,gather in records:
                            at=(s*capacity+counts[s])*6
                            fwd[at:at+6]=array('i',[0,t,primary,owner,row,gather])
                            counts[s]+=1
                        for i in range(start,counts[s]): fwd[(s*capacity+i)*6]=counts[s]
                        if len(ids)>1:
                            values=[struct.unpack('<128f',ledger[(s*tokens+t)*topk+slot][1]) for slot,e in ids]
                            partial[primary*512:(primary+1)*512]=struct.pack('<128f',*[sum(v[h] for v in values) for h in range(hidden)])
                            stats['reduce_rows']+=1
                        else: stats['direct_rows']+=1
                    for slot,e in ids[1:]:
                        row=(s*tokens+t)*topk+slot
                        if e//(experts//world)==rank and gateway!=rank:
                            sends[gateway%8][chunk].append([s,row,row])
                            stats['send_rows']+=1
                ranges[(s*chunks+chunk)*2+1]=counts[s]
        packed=array('q',[0])*(8*rows*3)
        backward_counts=[]
        for g in range(8):
            for chunk in range(chunks):
                run=sends[g][chunk]
                if run: run[-1][2]|=(chunk+1)<<32
                backward[g].extend(run)
            backward_counts.append(len(backward[g]))
            for i,record in enumerate(backward[g]): packed[(g*rows+i)*3:(g*rows+i)*3+3]=array('q',record)
        config=struct.pack('<17I',rank,world,tokens,hidden,topk,rows,rows,chunk_tokens,1,intra_cores+owner_cores,8,3,0,intra_cores,owner_cores,peer_slots,1)
        Path(str(meta)+'.config').write_bytes(config)
        Path(str(meta)+'.backward').write_bytes(packed.tobytes())
        Path(str(meta)+'.backward_counts').write_bytes(struct.pack('<8i',*backward_counts))
        Path(str(meta)+'.native_mem_info').write_bytes(images['native_mem_info'])
        subprocess.run([str(helper),str(meta)],check=True)
        raw=bytearray(Path(str(meta)+'.args').read_bytes())
        struct.pack_into('<Q',raw,80,0)
        layout=struct.unpack('<6I6Q',Path(str(meta)+'.layout').read_bytes())
        gather_offset,partial_offset,workspace_bytes=layout[6:9]
        assert len(raw)==288 and layout[:2]==(chunks,groups)
        images=runtime.runtime_images(abi,rank,workspace_bytes,world=world)
        ws=bytearray(workspace_bytes)
        for g in range(64): struct.pack_into('<Q',ws,(3*world+g)*512,1)
        ws[gather_offset:partial_offset]=GUARD*((partial_offset-gather_offset)//4)
        ws[partial_offset:]=GUARD*((workspace_bytes-partial_offset)//4)
        schedule = Path(str(meta)+'.schedule').read_bytes()
        assert len(schedule) == layout[11]
        ws[layout[10]:gather_offset] = schedule
        addresses=dict(runtime.BASES,input=(0x440000000 if len(inp)>0x1000000 else 0x404000000),prefill=0x405000000,masks=0x406000000,
                       backward=0x407000000,backward_counts=0x408000000,backward_ranges=0x409000000)
        images.update(workspace=ws,input=inp,forward=fwd.tobytes(),counts=struct.pack('<%di'%world,*counts),
                      ranges=struct.pack('<%di'%len(ranges),*ranges),masks=struct.pack('<%di'%len(masks),*masks),
                      backward=Path(str(meta)+'.backward').read_bytes(),backward_counts=struct.pack('<8i',*backward_counts),
                      backward_ranges=Path(str(meta)+'.backward_ranges').read_bytes(),status=b'\xa5'*512,
                      prefill=Path(str(meta)+'.prefill').read_bytes())
        completed=bytearray((len(masks)*8+8)*512)
        for c,mask in enumerate(masks):
            for owner in range(8):
                if mask&(1<<owner): struct.pack_into('<Q',completed,(c*8+owner)*512,1)
        gold=output/'fixture-data'/name
        gold.mkdir(parents=True)
        (gold/'golden-returned.bin').write_bytes(gathered)
        (gold/'golden-completed.bin').write_bytes(completed)
        (gold/'golden-output.bin').write_bytes(partial)
        initial_partial=bytes(ws[partial_offset:])
        (gold/'initial-output.bin').write_bytes(initial_partial)
        exports={k:{'addr':base+address,'size':size} for k,address,size in (
            ('returned',addresses['workspace']+gather_offset,len(gathered)),
            ('completed',addresses['workspace']+(3*world+96)*512,len(completed)),
            ('output',addresses['workspace']+partial_offset,len(partial)),('status',addresses['status'],512))}
        inputs=[dict(name=k,addr=base+addresses[k],data=v) for k,v in images.items()]
        guards=[]
        for key,addr in [('guard_before',addresses['workspace']-512),('guard_after',addresses['workspace']+workspace_bytes)]:
            inputs.append(dict(name=key,addr=base+addr,data=b'\xde'*512))
            guards.append(dict(name=key,addr=base+addr,size=512,expected_byte=222))
        if disk_backed:inputs=DiskImages(output/'.image-spool'/name,inputs)
        specs.append(dict(rank=rank,abi_size=len(raw),fields=[dict(name='word%d'%(i//8),offset=i,type='uint64_t',value=v)
                          for i,(v,) in zip(range(0,len(raw),8),struct.iter_unpack('<Q',raw))],
                          gm_size=runtime.GM_BYTES,sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,
                          param_base_addr=base+0x80000000,binary_addr=base+0x40000000,images=inputs,exports=exports,guards=guards))
    root='/work/cases/%dp-t%d-local-data'%(world,tokens)
    padded=output/'kernel-padded.o'
    binary=kernel.read_bytes()
    padded.write_bytes(binary+bytes(10485760-len(binary)))
    loader.prepare_loader(output/'loader',root,padded,loader.sha256(padded),'tilexr_moonep_combine_ht_kernel',specs)
    stats.update(runtime_root=root,sim_send=True,candidate_commit=revision,
                 kernel_sha256=loader.sha256(padded),routes_sha256=loader.sha256(output/'routes.json'))
    (output/'fixture-data/manifest.json').write_text(json.dumps(stats,indent=2))
    if disk_backed:
        shutil.rmtree(output/'.image-spool')
    (output/'identity.json').write_text(json.dumps(stats,indent=2))
    print(json.dumps(stats,separators=(',',':')))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--kernel',required=True)
    p.add_argument('--world',type=int,default=64)
    p.add_argument('--tokens',type=int,default=16)
    p.add_argument('--disk-backed-images',action='store_true')
    a=p.parse_args()
    prepare(a.source,a.output,a.world,a.tokens,a.kernel,a.disk_backed_images)
