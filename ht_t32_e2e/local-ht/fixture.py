import gzip
import hashlib
import json
import struct
import subprocess
import sys
import tarfile
from pathlib import Path

case=Path(__file__).resolve().parent
support=Path('/data/dhp/TileXR-combine-ht-final-reduce/tools/unitest/simulator/experiments/inter_server_send/sst')
sys.path.insert(0,str(support))
import loader
import runtime

def floats(values):
    return struct.pack(f'<{len(values)}f', *values)

def row(sender, token):
    return [sender * 8 + token + col / 64 for col in range(128)]

def emit(rank, result):
    name=f'rank{rank:02d}'
    local=rank%8
    base=rank * runtime.GM_BYTES
    abi=json.loads(subprocess.check_output(['/tmp/tilexr-ht-bs32.AbIjf4/abi-probe',str(rank),'4','128','8','4','4','2026']))
    images=runtime.runtime_images(abi,rank,1024*1024)
    (case/'remote-memory.bin').write_bytes(images['native_mem_info'])
    stem=case/'prefill-data'
    subprocess.run([case/'prefill',str(rank),case/'remote-memory.bin',stem],check=True)
    raw=bytearray(stem.with_suffix('.args').read_bytes())
    assert len(raw)==248
    config=struct.unpack_from('<13I',raw,112)
    assert config[:5] == (local,8,4,128,8)
    chunks,groups,slots,forward_capacity,_,_,gather,partial,workspace,_=struct.unpack_from('<6I4Q',raw,168)
    assert chunks==groups==1 and forward_capacity==32 and workspace<1024*1024
    images=runtime.runtime_images(abi,rank,workspace)
    addresses=runtime.BASES | dict(input=0x404000000,prefill=0x405000000,masks=0x406000000)
    ws=bytearray(workspace)
    for g in range(8):
        struct.pack_into('<Q',ws,(24+g)*512,1)
    images['workspace']=ws
    images['input']=floats([v for t in range(4) for v in row(local,t)])
    images['status']=b'\xa5'*512
    images['prefill']=stem.with_suffix('.bin').read_bytes()
    images['masks']=struct.pack('<I',255^(1<<local))
    fwd=bytearray(8*forward_capacity*24)
    start=0
    for token in range(4):
        stop=start+7
        for sender in range(8):
            if sender==local:
                continue
            struct.pack_into('<6i',fwd,(local*forward_capacity+start)*24,stop,token,token,sender,token,sender*4+token)
            start+=1
    images['forward']=fwd
    ranges=[0]*(8*2)
    ranges[local*2+1]=start
    images['ranges']=struct.pack('<16i',*ranges)
    inputs=[dict(name=k,addr=base+addresses[k],data=v) for k,v in images.items()]
    guards=[]
    for name_guard,loc in [('guard_before',addresses['workspace']-512),('guard_after',addresses['workspace']+workspace)]:
        inputs.append(dict(name=name_guard,addr=base+loc,data=b'\xde'*512))
        guards.append(dict(name=name_guard,addr=base+loc,size=512,expected_byte=222))
    for offset,key in ((0,'workspace'),(8,'input'),(16,'forward'),(32,'ranges'),(40,'masks'),(72,'status'),
                       (88,'commArgs'),(96,'udma_info'),(104,'registry'),(240,'prefill')):
        struct.pack_into('<Q',raw,offset,base+addresses[key])
    exports={name:dict(addr=base+addr,size=size) for name,addr,size in [
        ('returned',addresses['workspace']+gather,32*512),
        ('completed',addresses['workspace']+120*512,16*512),
        ('output',addresses['workspace']+partial,4*512),
        ('status',addresses['status'],512)]}
    gold=case/'fixture-data'/name
    gold.mkdir(parents=True)
    gold_return=bytearray(32*512)
    complete=bytearray(16*512)
    for sender in range(8):
        if sender != local:
            gold_return[sender*4*512:(sender+1)*4*512]=floats([v for token in range(4) for v in row(sender,token)])
            struct.pack_into('<Q',complete,sender*512,1)
    for owner in range(8):
        struct.pack_into('<I',complete,(8+owner)*512,1)
    (gold/'golden-returned.bin').write_bytes(gold_return)
    (gold/'golden-completed.bin').write_bytes(complete)
    (gold/'golden-output.bin').write_bytes(floats([sum(row(sender,token)[h] for sender in range(8))
                                                   for token in range(4) for h in range(128)]))
    result.append(dict(rank=rank,abi_size=len(raw),fields=[
        dict(name=f'word{i//8}',offset=i,type='uint64_t',value=v)
        for i,(v,) in zip(range(0,len(raw),8),struct.iter_unpack('<Q',raw))],
        gm_size=runtime.GM_BYTES,sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,
        param_base_addr=base+0x80000000,binary_addr=base+0x40000000,images=inputs,exports=exports,guards=guards))

ranks=[]
for rank in range(64):
    emit(rank,ranks)
kernel=case/'inter_server_send.o'
digest=loader.sha256(kernel)
(case/'inter_server_send.o.gz').write_bytes(gzip.compress(kernel.read_bytes(),mtime=0))
label='/work/inter-server-native/t4-local-ht-fp32-data'
entry='tilexr_moonep_combine_ht_kernel'
loader.prepare_loader(case/'loader',label,kernel,digest,entry,ranks)
identity=dict(tokens=4,hidden=128,world=64,local_ranks=8,chunks=1,entry=entry,kernel_sha256=digest,
              runtime_root=label,commit='29f973b0d322105957ccaf31e2d4539f6fe00a24')
(case/'fixture-data'/'manifest.json').write_text(json.dumps(identity))
tar=case/'fixture-images.tar.gz'
with tarfile.open(tar,'w:gz',compresslevel=3) as archive:
    for item in sorted((case/'fixture-data').rglob('*')):
        if item.is_file():
            archive.add(item,arcname=str(item.relative_to(case)),recursive=False)
    for item in sorted((case/'loader'/'input_bin').rglob('*.bin')):
        archive.add(item,arcname=str(item.relative_to(case/'loader')),recursive=False)
    for name in ('workload.yml','export-layout.json'):
        archive.add(case/'loader'/name,arcname=name,recursive=False)
identity['input_archive_sha256']=loader.sha256(tar)
(case/'fixture-identity.json').write_text(json.dumps(identity,indent=2)+'\n')
print(json.dumps(identity))
