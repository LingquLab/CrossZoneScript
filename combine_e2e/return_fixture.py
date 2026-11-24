# Copyright (c) 2026 TileXR Project.
"""Bind isolated return, inter-server and final kernels to verified upstream data."""
import argparse,hashlib,json,shutil,struct,sys
from pathlib import Path

MAGIC=2026
GUARD=struct.pack('<f',-17.)
ENTRY={'return':'tilexr_ep_ht_local_return_kernel','inter-send':'tilexr_ep_ht_inter_server_send_kernel',
       'return-independent':'tilexr_ep_ht_local_return_kernel','inter-send-independent':'tilexr_ep_ht_inter_server_send_kernel',
       'final-independent':'tilexr_ep_ht_final_reduce_kernel','final-joint':'tilexr_ep_ht_final_reduce_kernel'}

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def valid(case):
    p=case/'phase-result.json'
    assert p.is_file() and json.loads(p.read_text()).get('qualified') is True, 'predecessor_not_qualified'

def main(root,stage,abis):
    root=Path(root)
    world,tokens,hidden,topk=64,16,128,6
    source=root/'source'
    support=source/'tools/unitest/simulator/experiments/inter_server_send/sst'
    sys.path.insert(0,str(support))
    import loader,runtime
    independent=stage.endswith('-independent')
    kind='return' if stage.startswith('return') else ('inter-send' if stage.startswith('inter-send') else 'final')
    local=root/'cases'/('64p-t16-local-send-a1' if independent else '64p-t16-local-fused-a1')
    if not independent: valid(local)
    case=root/'cases'/('64p-t16-'+stage+'-a1')
    assert not case.exists(), 'case_already_exists'
    golden=case.with_name(case.name+'-golden')
    golden.mkdir(exist_ok=False)
    previous=case.with_name('64p-t16-'+('return' if stage=='inter-send' else 'inter-send')+'-a1')
    if stage in ('inter-send','final-joint'): valid(previous)
    routes=json.loads((local/'routes.json').read_text())
    assert len(routes)==world*tokens
    build=json.loads((root/'bootstrap-result.json').read_text())['build']['targets']
    target=ENTRY[stage]
    key='fp32-'+target
    binary=root/build[key]['path']
    assert digest(binary)==build[key]['sha256']
    padded=golden/'kernel.o'
    raw=binary.read_bytes()
    padded.write_bytes(raw+bytes(10485760-len(raw)))
    specs=[]
    provenance=hashlib.sha256()
    for rank,abi in enumerate(abis):
        config={x['name'].split('.',1)[1]:x['value'] for x in abi['fields'][6:]}
        assert config['world']==64 and config['rank']==rank and config['tokens']==16 and config['hidden']==128
        c=bytearray(112)
        for field in abi['fields'][6:]:
            fmt={'uint32_t':'I','uint64_t':'Q','int64_t':'q'}[field['type']]
            struct.pack_into('<'+fmt,c,field['offset']-48,field['value'])
        base=rank*runtime.GM_BYTES
        pointers=dict(runtime.BASES,localWorkspace=0x40a000000,localStatus=0x40b000000,
                      serverMask=0x40c000000,output=0x40e000000)
        local_ready=config['workspaceBytes']
        registered=local_ready+8*512+world*512
        ws=bytearray(registered)
        name='rank%02d'%rank
        inp=(local/'input_bin'/name/'input.bin').read_bytes()
        partial=(local/'fixture-data'/name/'golden-output.bin').read_bytes() if independent else (local/'actual'/name/'output.bin').read_bytes()
        provenance.update(hashlib.sha256(partial).digest())
        ws[config['inputOffset']:config['inputOffset']+len(inp)]=inp
        ws[config['partialOffset']:config['partialOffset']+len(partial)]=partial
        returned=bytearray(GUARD*((config['signalOffset']-config['returnOffset'])//4))
        ws[config['returnOffset']:config['signalOffset']]=returned
        completed=bytearray(config['returnOffset']-config['readyOffset'])
        local_flags=bytearray(8*512)
        gold_returned=bytearray(returned)
        final=bytearray()
        masks=[]
        for token in range(tokens):
            route=routes[rank*tokens+token]
            assert route['source']==rank and route['token']==token
            server_values={}
            all_values=[]
            for slot,e in enumerate(route['experts']):
                values=[1+(rank*17+token*3+e*5+slot*11+h%13)/256 for h in range(hidden)]
                server=e//(256//world)//8
                server_values.setdefault(server,[]).append(values)
                all_values.append(values)
            masks.append(sum(1<<server for server in server_values))
            final.extend(struct.pack('<128f',*[sum(v[h] for v in all_values) for h in range(hidden)]))
            for server,values in server_values.items():
                if kind=='return' and server!=rank//8: continue
                offset=(token*8+server)*512
                gold_returned[offset:offset+512]=struct.pack('<128f',*[sum(v[h] for v in values) for h in range(hidden)])
        for reducer in range(40): struct.pack_into('<qq',completed,reducer*512,MAGIC,4)
        for gateway in range(8): struct.pack_into('<q',local_flags,gateway*512,MAGIC)
        if kind!='return':
            for gateway in range(world):
                if gateway//8==rank//8: continue
                for lane in range(2): struct.pack_into('<q',completed,(40+(gateway*4+3)*2+lane)*512,MAGIC)
        if stage in ('inter-send','final-joint'):
            actual=previous/'actual'/name
            for field,start,end in (('returned',config['returnOffset'],config['signalOffset']),
                                    ('completed',config['readyOffset'],config['returnOffset']),
                                    ('output',local_ready,local_ready+4096)):
                payload=(actual/(field+'.bin')).read_bytes()
                assert len(payload)==end-start
                ws[start:end]=payload
                provenance.update(hashlib.sha256(payload).digest())
            if stage=='inter-send':
                assert ws[local_ready:local_ready+4096]==local_flags
            else:
                assert ws[config['readyOffset']:config['returnOffset']]==completed
                assert ws[config['returnOffset']:config['signalOffset']]==gold_returned
        elif stage=='final-independent':
            ws[config['readyOffset']:config['returnOffset']]=completed
            ws[config['returnOffset']:config['signalOffset']]=gold_returned
            ws[local_ready:local_ready+4096]=local_flags
        elif stage=='inter-send-independent':
            ws[config['readyOffset']:config['readyOffset']+40*512]=completed[:40*512]
            ws[local_ready:local_ready+4096]=local_flags
            for token in range(tokens):
                offset=(token*8+rank//8)*512
                start=config['returnOffset']+offset
                ws[start:start+512]=gold_returned[offset:offset+512]
        images=runtime.runtime_images(abi,rank,registered,world=world)
        images.update(workspace=ws,forward=(local/'input_bin'/name/'forward.bin').read_bytes(),
                      counts=(local/'input_bin'/name/'counts.bin').read_bytes(),ranges=(local/'input_bin'/name/'ranges.bin').read_bytes(),
                      status=b'\xa5'*512)
        if kind=='return':
            local_ws=bytearray((local/'input_bin'/name/'workspace.bin').read_bytes())
            old_args=(local/'input_bin'/name/'kernel_args.bin').read_bytes()
            old_workspace=struct.unpack_from('<Q',old_args,0)[0]
            old_layout=json.loads((local/'export-layout.json').read_text())['ranks'][rank]
            for field in ('returned','completed','output'):
                bounds=old_layout['exports'][field]
                data=(local/'fixture-data'/name/('golden-'+field+'.bin')).read_bytes() if independent else (local/'actual'/name/(field+'.bin')).read_bytes()
                offset=bounds['addr']-old_workspace
                local_ws[offset:offset+len(data)]=data
            if independent:
                for reducer in range(8):struct.pack_into('<I',local_ws,(3*world+96+4*8+reducer)*512,4)
                local_status=bytes(32)+b'\xa5'*480
            else:local_status=(local/'actual'/name/'status.bin').read_bytes()
            images.update(localWorkspace=local_ws,localStatus=local_status)
            args=struct.pack('<8Q',*[base+pointers[k] for k in ('commArgs','workspace','localWorkspace','forward','counts','ranges','localStatus','status')])
            args+=c+struct.pack('<2Q2I',(3*world+96+4*8)*512,local_ready,4,8)
            assert len(args)==200
            blocks=8
        elif kind=='inter-send':
            args=struct.pack('<6Q',*[base+pointers[k] for k in ('commArgs','workspace','forward','counts','ranges','status')])+c
            assert len(args)==160
            blocks=16
        else:
            images.update(serverMask=struct.pack('<16Q',*masks),output=b'\xa5'*len(final))
            args=struct.pack('<5Q',base+pointers['workspace'],base+pointers['serverMask'],base+pointers['workspace']+local_ready,
                             base+pointers['output'],base+pointers['status'])+c
            assert len(args)==152
            blocks=16
        out=golden/name
        out.mkdir()
        for field,data in (('returned',gold_returned),('completed',completed),
                           ('output',final if stage.startswith('final') else local_flags)):
            (out/('golden-'+field+'.bin')).write_bytes(data)
        status=bytearray(b'\xa5'*512)
        for block in range(blocks): struct.pack_into('<I',status,block*32,0)
        (out/'golden-status.bin').write_bytes(status)
        exports={'returned':{'addr':base+pointers['workspace']+config['returnOffset'],'size':len(returned)},
                 'completed':{'addr':base+pointers['workspace']+config['readyOffset'],'size':len(completed)},
                 'output':{'addr':base+pointers['output'],'size':len(final)} if stage.startswith('final') else
                          {'addr':base+pointers['workspace']+local_ready,'size':4096},
                 'status':{'addr':base+pointers['status'],'size':512}}
        guards=[]
        for key,addr in (('guard_before',pointers['workspace']-512),('guard_after',pointers['workspace']+registered)):
            images[key]=b'\xde'*512
            pointers[key]=addr
            guards.append(dict(name=key,addr=base+addr,size=512,expected_byte=222))
        if stage.startswith('final'):
            for key,addr in (('output_guard_before',pointers['output']-512),('output_guard_after',pointers['output']+len(final))):
                images[key]=b'\xde'*512
                pointers[key]=addr
                guards.append(dict(name=key,addr=base+addr,size=512,expected_byte=222))
        specs.append(dict(rank=rank,abi_size=len(args),fields=[dict(name='word%d'%(i//8),offset=i,type='uint64_t',value=v)
                          for i,(v,) in zip(range(0,len(args),8),struct.iter_unpack('<Q',args))],gm_size=runtime.GM_BYTES,
                          sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,param_base_addr=base+0x80000000,
                          binary_addr=base+0x40000000,images=[dict(name=k,addr=base+pointers[k],data=v) for k,v in images.items()],
                          exports=exports,guards=guards))
    runtime_root='/work/cases/'+case.name
    loader.prepare_loader(case,runtime_root,padded,digest(padded),ENTRY[stage],specs)
    padded.unlink()
    shutil.move(str(golden),str(case/'fixture-data'))
    workload=json.loads((case/'workload.yml').read_text())
    for chip in workload['workload_config'].values(): chip['block_dim']=blocks
    (case/'workload.yml').write_text(json.dumps(workload,indent=2))
    meta={'world':64,'tokens':16,'hidden':128,'topk':6,'experts':256,'stage':stage,'dtype':'fp32',
          'candidate_commit':'013cc3e2f8abf8157abdc609afcfe2369806b1e1','entry':ENTRY[stage],
          'kernel_sha256':digest(case/'kernel/inter_server_send.o'),'routes_sha256':digest(local/'routes.json'),
          'input_mode':'independent_known_inputs' if independent else 'actual_predecessor_outputs',
          'consumed_actual_sha256':None if independent else provenance.hexdigest(),
          'consumed_input_sha256':provenance.hexdigest(),'blocks':blocks}
    (case/'fixture-data/manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,separators=(',',':')))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root',required=True)
    p.add_argument('--stage',choices=list(ENTRY),required=True)
    p.add_argument('--abis',type=Path,required=True)
    a=p.parse_args()
    main(a.root,a.stage,json.loads(a.abis.read_text()))
