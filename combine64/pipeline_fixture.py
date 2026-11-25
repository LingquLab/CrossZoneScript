# Copyright (c) 2026 TileXR Project.
"""Build one-invocation HT inputs with distinct local and return queue resources."""
import argparse,hashlib,json,shutil,struct,subprocess,sys
import profile as ht_profile
from pathlib import Path

LOCAL_WS=0x200000000
RETURN_WS=0x220000000
EXTRA={'localComm':0x2ff100000,'localInfo':0x300080000,'localRegistry':0x3000a0000,
       'localSq':0x317000000,'localCq':0x318000000,'localMem':0x319000000,
       'prefill':0x405000000,'masks':0x406000000,'backward':0x407000000,
       'backward_counts':0x408000000,'backward_ranges':0x409000000,
       'profiling':0x432000000,'local_profiling':ht_profile.ADDRESS,'returnForward':0x40d000000,'serverMask':0x40c000000,'output':0x40e000000,'barriers':0x40f000000}
GUARD=struct.pack('<f',-17.)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def local_slot(g):return (g%2)*20+16+g//2

def main(root,directory,abis,tokens=4,attempt="a1",build_variant="original"):
    assert attempt in ("a1","a2","a3","a4","a5","a6")
    root,directory=Path(root),Path(directory)
    local=root/'assets'/('64p-t%d'%tokens)
    asset_identity=json.loads((local/'identity.json').read_text())
    assert asset_identity['candidate_commit']=='40596e2d9d454b7a7b00b4b34b96d49158e1c3a3'
    assert (asset_identity['world'],asset_identity['tokens'],asset_identity['hidden'],asset_identity['topk'])==(64,tokens,128,6)
    assert build_variant in ('original','scalar-dcci','isolated-inter','leader-barrier','four-module-joint','four-module-sq-align512')
    build_root=root/{'original':'pipeline-build-40596e2-a1','scalar-dcci':'pipeline-scalar-dcci-a1','isolated-inter':'pipeline-isolated-inter-a1','leader-barrier':'pipeline-leader-barrier-a1','four-module-joint':'pipeline-four-module-joint-a1','four-module-sq-align512':'pipeline-four-module-sq-align512-a1'}[build_variant]
    extra_fence=build_variant not in ('four-module-joint','four-module-sq-align512')
    build=json.loads((build_root/'build-result.json').read_text())
    assert build['compile_exit']==build['link_exit']==0 and build['abi_bytes']==512
    case=root/'cases'/('64p-t%d-pipeline-%s'%(tokens,attempt))
    assert not case.exists()
    temporary=case.with_name(case.name+'-preparation')
    temporary.mkdir(exist_ok=False)
    binary=build_root/'kernel.o'
    assert sha(binary)==build['sha256']
    padded=temporary/'kernel.o'
    data=binary.read_bytes();padded.write_bytes(data+bytes(10485760-len(data)))
    source=root/'source'
    common=source/'src/moonep/combine_ht/common'
    helper=temporary/'prefill-tool'
    subprocess.run(['c++','-std=c++14','-O2','-DTILEXR_COMBINE_HT_SST_FP32=1','-DTILEXR_COMBINE_HT_SIM_SEND=1','-I'+str(source/'include'),
                    '-I'+str(common),str(directory/'prefill.cpp'),
                    str(source/'src/moonep/combine_ht/host/combine_ht_prefill.cpp'),'-o',str(helper)],check=True)
    sys.path.insert(0,str(source/'tools/unitest/simulator/experiments/inter_server_send/sst'))
    import loader,runtime
    original_workspace=runtime.BASES['workspace']
    assert original_workspace==LOCAL_WS
    routes=json.loads((local/'routes.json').read_text())
    assert len(routes)==64*tokens
    specs=[]
    golden=temporary/'fixture-data';golden.mkdir()
    for rank,abi in enumerate(abis):
        c={f['name'].split('.',1)[1]:f['value'] for f in abi['fields'][6:]}
        assert (c['rank'],c['world'],c['tokens'],c['hidden'],c['topk'])==(rank,64,tokens,128,6)
        config=bytearray(112)
        for field in abi['fields'][6:]:struct.pack_into('<'+{'uint32_t':'I','uint64_t':'Q','int64_t':'q'}[field['type']],config,field['offset']-48,field['value'])
        local_ready=c['workspaceBytes']
        return_bytes=local_ready+4096+64*512
        base=rank*runtime.GM_BYTES
        name='rank%02d'%rank
        old=local/'loader/input_bin'/name
        old_args=(old/'kernel_args.bin').read_bytes()
        local_ws=bytearray((old/'workspace.bin').read_bytes())
        layout=struct.unpack_from('<6I4Q',old_args,168)
        gather_offset,partial_offset,local_bytes=layout[6:9]
        assert len(local_ws)==local_bytes
        runtime.BASES['workspace']=RETURN_WS
        try:images=runtime.runtime_images(abi,rank,return_bytes,world=64)
        finally:runtime.BASES['workspace']=original_workspace
        addresses=dict(runtime.BASES,**EXTRA,returnWorkspace=RETURN_WS)
        # The model sees forty physical slots. Local traffic uses the eight
        # remaining slots while return traffic retains the checked 32-slot map.
        physical_mem=bytearray(images['mem_info'])
        width=40
        local_sq,local_cq,local_mem=bytearray(),bytearray(),bytearray()
        helper_memory=bytearray(64*32*width)
        for peer in range(64):
            for g in range(8):
                physical=local_slot(g)
                assert physical not in {runtime.physical_qp(q) for q in range(32)}
                index=peer*40+physical
                m=bytearray(physical_mem[index*width:(index+1)*width])
                registered=(local_bytes+2097151)//2097152*2097152
                struct.pack_into('<I',m,16,registered)
                struct.pack_into('<Q',m,24,peer*runtime.GM_BYTES+LOCAL_WS)
                physical_mem[index*width:(index+1)*width]=m
                local_sq.extend(images['sq_ctx'][index*80:(index+1)*80])
                local_cq.extend(images['cq_ctx'][index*56:(index+1)*56])
                local_mem.extend(m)
                helper_memory[(peer*32+g)*width:(peer*32+g+1)*width]=m
        images['mem_info']=physical_mem
        a=abi['runtime_abi']
        local_comm=bytearray(images['commArgs'])
        struct.pack_into('<I',local_comm,16,0x400|0x1000)
        for peer in range(64):struct.pack_into('<Q',local_comm,a['comm_peer_mems']+peer*8,peer*runtime.GM_BYTES+LOCAL_WS)
        for field,address in (('comm_udma_info',EXTRA['localInfo']),('comm_udma_registry',EXTRA['localRegistry'])):
            struct.pack_into('<Q',local_comm,a[field],base+address)
        struct.pack_into('<Q',local_comm,a['comm_peer_bytes'],local_bytes)
        local_registry=bytearray(images['registry'])
        for peer in range(64):struct.pack_into('<QQ',local_registry,a['registry_regions']+peer*16,peer*runtime.GM_BYTES+LOCAL_WS,local_bytes)
        local_info=bytearray(images['udma_info'])
        struct.pack_into('<I',local_info,0,8)
        for field,address in (('info_sq',EXTRA['localSq']),('info_cq',EXTRA['localCq']),('info_mem',EXTRA['localMem'])):
            struct.pack_into('<Q',local_info,a[field],base+address)
        images.update(localComm=local_comm,localInfo=local_info,localRegistry=local_registry,
                      localSq=local_sq,localCq=local_cq,localMem=local_mem)
        stem=temporary/name
        Path(str(stem)+'.config').write_bytes(old_args[112:164])
        for source_name,suffix in (('backward.bin','.backward'),('backward_counts.bin','.backward_counts')):
            Path(str(stem)+suffix).write_bytes((old/source_name).read_bytes())
        Path(str(stem)+'.native_mem_info').write_bytes(helper_memory)
        subprocess.run([str(helper),str(stem),str(RETURN_WS)],check=True)
        raw=bytearray(Path(str(stem)+'.args').read_bytes())
        struct.pack_into('<Q',raw,80,base+ht_profile.ADDRESS)
        for offset,address in ((8,RETURN_WS),(88,EXTRA['localComm']),(96,EXTRA['localInfo']),(104,EXTRA['localRegistry'])):
            struct.pack_into('<Q',raw,offset,base+address)
        assert len(raw)==248 and struct.unpack_from('<I',raw,156)[0]==3
        returned=bytearray(return_bytes)
        inp=(old/'input.bin').read_bytes()
        returned[:len(inp)]=inp
        returned[c['partialOffset']:c['partialOffset']+len(inp)]=GUARD*(len(inp)//4)
        returned[c['returnOffset']:c['signalOffset']]=GUARD*((c['signalOffset']-c['returnOffset'])//4)
        gold=bytearray(returned)
        masks=[];final=bytearray()
        for token in range(tokens):
            selected=routes[rank*tokens+token]['experts']
            servers={};all_values=[]
            for slot,e in enumerate(selected):
                value=[1+(rank*17+token*3+e*5+slot*11+h%13)/256 for h in range(128)]
                servers.setdefault(e//4//8,[]).append(value);all_values.append(value)
            masks.append(sum(1<<s for s in servers))
            final.extend(struct.pack('<128f',*[sum(v[h] for v in all_values) for h in range(128)]))
            for server,values in servers.items():
                start=c['returnOffset']+(token*8+server)*512
                gold[start:start+512]=struct.pack('<128f',*[sum(v[h] for v in values) for h in range(128)])
        for r in range(40):struct.pack_into('<qq',gold,c['readyOffset']+r*512,2026,4)
        for g in range(64):
            if g//8!=rank//8:
                for lane in range(2):struct.pack_into('<q',gold,c['completionOffset']+((g*4+3)*2+lane)*512,2026)
            if extra_fence:struct.pack_into('<q',gold,local_ready+4096+g*512,2026)
        for owner in range(16):struct.pack_into('<q',gold,c['signalOffset']+owner*512,2026)
        for gateway in range(8):struct.pack_into('<q',gold,local_ready+gateway*512,2026)
        packed_forward=(old/'forward.bin').read_bytes()
        return_forward=b''.join(struct.pack('<8i',*record,0,0) for record in struct.iter_unpack('<6i',packed_forward))
        assert abi['forward_record_words']==8
        images.update(profiling=ht_profile.image(base+EXTRA['profiling'],32),
                      local_profiling=(old/'profiling.bin').read_bytes(),returnForward=return_forward,
                      workspace=local_ws,returnWorkspace=returned,status=b'\xa5'*3072,
                      forward=(old/'forward.bin').read_bytes(),counts=(old/'counts.bin').read_bytes(),ranges=(old/'ranges.bin').read_bytes(),
                      masks=(old/'masks.bin').read_bytes(),backward=(old/'backward.bin').read_bytes(),
                      backward_counts=(old/'backward_counts.bin').read_bytes(),backward_ranges=(old/'backward_ranges.bin').read_bytes(),
                      prefill=Path(str(stem)+'.prefill').read_bytes(),serverMask=struct.pack('<%dQ'%tokens,*masks),
                      output=b'\xa5'*len(final),barriers=bytes(8192))
        status_base=base+runtime.BASES['status']
        returned_args=struct.pack('<8Q',base+runtime.BASES['commArgs'],base+RETURN_WS,base+LOCAL_WS,
                                  base+EXTRA['returnForward'],base+runtime.BASES['counts'],base+runtime.BASES['ranges'],status_base,status_base+512)
        returned_args+=config+struct.pack('<2Q2I',(3*64+96+32)*512,local_ready,4,8)
        packed=raw+returned_args+struct.pack('<7Q',base+EXTRA['serverMask'],base+EXTRA['output'],status_base+1024,
                                            status_base+1536,status_base+2048,base+EXTRA['barriers'],status_base+2560)+struct.pack('<Q',base+EXTRA['profiling'])
        assert len(packed)==512
        out=golden/name;out.mkdir()
        golden_status=bytearray(b'\xa5'*3072)
        for i in range(8):struct.pack_into('<I',golden_status,i*4,0)
        stage_statuses=[(512,8),(1024,16),(1536,16),(2560,16)]
        if extra_fence:stage_statuses.append((2048,1))
        for offset,count in stage_statuses:
            for block in range(count):struct.pack_into('<I',golden_status,offset+block*32,0)
        barrier_gold=bytearray(8192)
        final_epoch=6 if extra_fence else 5
        for block in range(16):struct.pack_into('<I',barrier_gold,block*512,final_epoch)
        if build_variant in ('leader-barrier','four-module-joint','four-module-sq-align512'):struct.pack_into('<I',barrier_gold,8160,final_epoch)
        gold_values={'returned':gold[c['returnOffset']:c['signalOffset']],
                     'completed':gold[c['readyOffset']:], 'output':final,'status':golden_status,
                     'gather':(local/'fixture-data'/name/'golden-returned.bin').read_bytes(),
                     'partial':(local/'fixture-data'/name/'golden-output.bin').read_bytes(),'barriers':barrier_gold}
        for field,value in gold_values.items():(out/('golden-'+field+'.bin')).write_bytes(value)
        exports={key:{'addr':address,'size':len(gold_values[key])} for key,address in (
            ('returned',base+RETURN_WS+c['returnOffset']),('completed',base+RETURN_WS+c['readyOffset']),
            ('output',base+EXTRA['output']),('status',status_base),('gather',base+LOCAL_WS+gather_offset),
            ('partial',base+LOCAL_WS+partial_offset),('barriers',base+EXTRA['barriers']))}
        exports['profiling']={'addr':base+EXTRA['profiling'],'size':ht_profile.layout(32)['bytes']}
        exports['local_profiling']={'addr':base+EXTRA['local_profiling'],'size':len(images['local_profiling'])}
        guards=[]
        for label,address,size in (('local',LOCAL_WS,local_bytes),('return',RETURN_WS,return_bytes),
                                   ('output',EXTRA['output'],len(final)),('status',runtime.BASES['status'],3072),('barrier',EXTRA['barriers'],8192)):
            for suffix,at in (('before',address-512),('after',address+size)):
                key=label+'_'+suffix
                images[key]=b'\xde'*512;addresses[key]=at
                guards.append(dict(name=key,addr=base+at,size=512,expected_byte=222))
        specs.append(dict(rank=rank,abi_size=512,fields=[dict(name='word%d'%(i//8),offset=i,type='uint64_t',value=v)
                          for i,(v,) in zip(range(0,512,8),struct.iter_unpack('<Q',packed))],gm_size=runtime.GM_BYTES,
                          sys_va_base_addr=base,ub_base_addr=0x80000,stack_base_addr=0x100000,param_base_addr=base+0x80000000,
                          binary_addr=base+0x40000000,images=[dict(name=k,addr=base+addresses[k],data=v) for k,v in images.items()],
                          exports=exports,guards=guards))
    loader.prepare_loader(case,'/work/cases/'+case.name,padded,sha(padded),'tilexr_ht_pipeline_sst',specs)
    shutil.move(str(golden),str(case/'fixture-data'))
    layout=json.loads((case/'export-layout.json').read_text())
    layout['required_exports']=['returned','completed','output','status','gather','partial','barriers','profiling','local_profiling']
    (case/'export-layout.json').write_text(json.dumps(layout,indent=2))
    meta={'world':64,'tokens':tokens,'profiling_capacity':32,'blocks':16,'hidden':128,'topk':6,'experts':256,'entry':'tilexr_ht_pipeline_sst',
          'candidate_commit':'40596e2d9d454b7a7b00b4b34b96d49158e1c3a3','kernel_sha256':sha(case/'kernel/inter_server_send.o'),
          'wrapper_sha256':build['sha256'],'pipeline_build_variant':build_variant,'compiler_scalar_dcci':build_variant=='scalar-dcci','stage_isolation_noinline':build_variant in ('isolated-inter','leader-barrier','four-module-joint','four-module-sq-align512'),'barrier_algorithm':'leader_release' if build_variant in ('leader-barrier','four-module-joint','four-module-sq-align512') else 'all_lanes_poll_all','extra_round_fence':extra_fence,'routes_sha256':sha(local/'routes.json'),'joint_single_invocation':True,'joint_input_mode':'initial_expert_outputs_and_routes',
          'local_physical_qps':[local_slot(g) for g in range(8)],'return_physical_qps':[runtime.physical_qp(q) for q in range(32)]}
    if build_variant=='four-module-sq-align512':
        meta.update({k:build[k] for k in ('kernel_compatibility_variant','compatibility_header_sha256','compatibility_baseline_header_sha256')})
    (case/'fixture-data/manifest.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps(meta,separators=(',',':')))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--build-variant',choices=('original','scalar-dcci','isolated-inter','leader-barrier','four-module-joint','four-module-sq-align512'),default='original');p.add_argument('--attempt',choices=('a1','a2','a3','a4','a5','a6'),default='a1');p.add_argument('--tokens',type=int,choices=(4,16,32,64,128),default=4);p.add_argument('--root',required=True);p.add_argument('--tools',required=True);p.add_argument('--abis',type=Path,required=True)
    a=p.parse_args();main(a.root,a.tools,json.loads(a.abis.read_text()),a.tokens,a.attempt,a.build_variant)
