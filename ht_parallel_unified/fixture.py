"""Construct the registered workspace and preserve ownership of the two SQ tables."""
import struct


def fullmesh(abi, rank, runtime, images):
    layout = abi['runtime_abi']
    rank_base = rank * runtime.GM_BYTES
    table = bytearray(images['udma_info'])
    struct.pack_into('<I', table, 0, 8)
    names = (('sq', 'info_sq', 'sq_bytes', 'sq_ctx'),
             ('cq', 'info_cq', 'cq_bytes', 'cq_ctx'),
             ('mem', 'info_mem', 'mem_bytes', 'mem_info'))
    result = {}
    for kind, address, width_name, source in names:
        label = 'fullmesh_' + kind
        struct.pack_into('<Q', table, layout[address], rank_base + EXTRA[label])
        width = layout[width_name]
        raw = images[source]
        data = bytearray()
        for peer in range(64):
            scratch_slots = set(PRODUCER_QUEUE)
            assert not scratch_slots & {runtime.physical_qp(qp) for qp in range(32)}
            for slot in PRODUCER_QUEUE:
                start = (peer * runtime.PHYSICAL_QPS + slot) * width
                data.extend(raw[start:start+width])
        result[label] = data
    result['fullmesh_info'] = table
    return result


def values(rank, token):
    return tuple(rank * 8 + token + column / 64 for column in range(HIDDEN))


def partial_values(server, token):
    return tuple(map(sum, zip(*(values(server * 8 + lane, token) for lane in range(8)))))


def f32(row):
    return struct.pack('<128f', *row)


def make_golden(rank, config, original_status):
    server = rank//8
    gather = b''.join(bytes(ROW) if lane==rank%8 else f32(values(server*8+lane, token))
                     for lane in range(8) for token in range(TOKENS))
    gather_ready = b''.join(struct.pack('<Q', 0 if lane==rank%8 else 1).ljust(ROW,b'\0')
                          for chunk in range(CHUNKS) for lane in range(8))
    partial = b''.join(f32(partial_values(server, token)) for token in range(TOKENS)) * 2
    ready = struct.pack('<qii',MAGIC,CHUNKS,0).ljust(ROW,b'\0')*8
    local_ready = struct.pack('<q',MAGIC).ljust(ROW,b'\0')*8
    returned = bytearray(config['signalOffset']-config['returnOffset'])
    completed = bytearray(config['returnOffset']-config['completionOffset'])
    output = bytearray()
    for token in range(TOKENS):
        for source in (server,server^1):
            begin=(token*8+source)*ROW
            returned[begin:begin+ROW]=f32(partial_values(source,token))
        output.extend(f32(map(sum,zip(partial_values(server,token),partial_values(server^1,token)))))
    for peer in range(64):
        if peer//8 != server:
            for lane in range(2):
                slot = (peer*CHUNKS + CHUNKS-1)*2 + lane
                struct.pack_into('<q',completed,ROW*slot,MAGIC)
    status=bytearray(original_status)
    status[:16*4]=bytes(16*4)
    for start,count in ((256,8),(512,16),(1024,16)):
        for worker in range(count):
            struct.pack_into('<I',status,start+worker*32,0)
    return dict(gather=gather,gather_ready=gather_ready,partial=partial,ready=ready,
                local_ready=local_ready,returned=returned,completed=completed,output=output,status=status)


def build_rank(rank, abi, raw, prefill, modules):
    native, runtime = modules
    config = native.config_values(abi)
    assert (config['rank'],config['tokens'],config['hidden'],config['chunks'],
            config['reducerCount'],config['magic'],config['forwardCapacity']) == (rank,TOKENS,256,CHUNKS,8,MAGIC,8*TOKENS)
    base = rank * runtime.GM_BYTES
    addresses = {name:base+address for name,address in (runtime.BASES | EXTRA).items()}
    total = config['workspaceBytes']
    local_offset = total + 2*8*ROW
    layout = struct.unpack_from('<6I4Q',raw,168)
    assert layout[:2] == (CHUNKS,1)
    gather_offset, partial_offset = layout[6:8]
    assert partial_offset - gather_offset == 8*TOKENS*ROW
    status_offset = local_offset + partial_offset
    status_bytes = 1536
    workspace = bytearray(status_offset + status_bytes)
    data = build_routes(rank, lambda server, local, token, column: values(server*8+local,token)[column],
                        TOKENS, CHUNKS)
    workspace[:len(data['input'])] = data['input']
    # Prefilled SO source words live in the attached workspace control area.
    for gateway in range(8):
        struct.pack_into('<Q',workspace,local_offset+(3*64+gateway)*ROW,1)
    status = b'\xff'*status_bytes
    workspace[status_offset:] = status
    images = runtime.runtime_images(abi, rank, len(workspace))
    images.update(fullmesh(abi,rank,runtime,images))
    images.update({key:value for key,value in data.items() if key in addresses})
    images.update(prefill=prefill,workspace=workspace,output=b'\x00\x00\xc0\x7f'*(TOKENS*HIDDEN))
    # Attach the exact native by-value structure, followed by five GM pointers.
    args = bytearray(raw)
    pointer = addresses['workspace']
    for index,value in enumerate((pointer+local_offset,pointer,addresses['forward'],addresses['counts'],
                                  addresses['ranges'],addresses['masks'],0,0,0,pointer+status_offset,
                                  0,addresses['commArgs'],addresses['fullmesh_info'],addresses['registry'])):
        struct.pack_into('<Q',args,index*8,value)
    struct.pack_into('<Q',args,240,addresses['prefill'])
    args.extend(struct.pack('<3Qq2I',local_offset,pointer+config['partialOffset'],
                            pointer+config['readyOffset'],MAGIC,2*TOKENS,0))
    raw_fields = [dict(name='arg_'+str(offset),type='uint64_t',offset=offset,value=value[0])
                  for offset,value in zip(range(0,288,8),struct.iter_unpack('<Q',args),strict=True)]
    raw_fields.extend(dict(field,offset=field['offset']+240) for field in abi['fields'][6:])
    values_at_tail = (addresses['server_mask'],addresses['output'],pointer+status_offset+256,
                      pointer+status_offset+512,pointer+status_offset+1024,total+8*ROW,(16<<32),
                      addresses['return_forward'])
    raw_fields.extend(dict(name='arg_'+str(offset),type='uint64_t',offset=offset,value=value)
                      for offset,value in zip(range(400,464,8),values_at_tail,strict=True))
    golden = make_golden(rank,config,status)
    export_offsets = dict(gather=local_offset+gather_offset,gather_ready=local_offset+(3*64+96)*ROW,
                          partial=config['partialOffset'],ready=config['readyOffset'],local_ready=total,
                          returned=config['returnOffset'],completed=config['completionOffset'],status=status_offset)
    for field, offset in export_offsets.items():
        zone=workspace[offset:offset+len(golden[field])]
        if field != 'status':
            assert not any(zone), (rank,field)
    exports = {name:dict(addr=pointer+offset,size=len(golden[name])) for name,offset in export_offsets.items()}
    exports['output']=dict(addr=addresses['output'],size=len(golden['output']))
    memory = [dict(name=name,addr=addresses[name],data=image) for name,image in images.items()]
    guards = []
    for name in ('workspace','output'):
        for suffix, offset in (('before',-ROW),('after',len(images[name]))):
            label=f'guard_{name}_{suffix}'
            address=addresses[name]+offset
            guards.append(dict(name=label,addr=address,size=ROW,expected_byte=222))
            memory.append(dict(name=label,addr=address,data=b'\xde'*ROW))
    trace = dict(workspace=pointer,return_offset=config['returnOffset'],partial_offset=config['partialOffset'],
                 ready_offset=config['readyOffset'],offset_map=export_offsets)
    binding = dict(rank=rank,abi_size=464,fields=raw_fields,images=memory,exports=exports,guards=guards,
                   gm_size=runtime.GM_BYTES,sys_va_base_addr=base,ub_base_addr=0x80000,
                   stack_base_addr=0x100000,param_base_addr=base+0x80000000,binary_addr=base+0x40000000)
    assert set(exports)==set(golden)==set(NAMES)
    return binding, golden, trace
