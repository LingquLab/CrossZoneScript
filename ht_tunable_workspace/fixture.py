"""Bind the single-workspace SST launch and derive independent route expectations."""
from pathlib import Path
import struct
import sys

SHARED = Path(__file__).resolve().parent.parent / 'ht_parallel_unified'
sys.path.insert(0, str(SHARED))
import config
import fixture as shared_fixture
import route

ENTRY = 'tilexr_ht_pipeline_sst'
TOKENS = None
BLOCKS = 48
ROW = config.ROW
MAGIC = config.MAGIC
NAMES = config.NAMES
ROLES = dict(gather=(0, 8, 4), reduce=(96, 8, 4), local_return=(256, 8, 32),
             cross_return=(512, 16, 32), final=(1024, 16, 32))

for name in ('EXTRA', 'PRODUCER_QUEUE', 'MAGIC', 'ROW', 'CHUNKS', 'HIDDEN'):
    setattr(shared_fixture, name, getattr(config, name))
shared_fixture.TOKENS = TOKENS


def build_rank(rank, abi, raw, prefill, modules):
    _, runtime = modules
    base = rank * runtime.GM_BYTES
    addresses = {name: base + address for name, address in (runtime.BASES | config.EXTRA).items()}
    workspace_address = addresses['workspace']
    assert len(raw) == 552 and len(abi['pipeline_fields']) == 93
    types = {'GM_ADDR': 'Q', 'uint64_t': 'Q', 'int64_t': 'q', 'uint32_t': 'I'}
    fields = [{**field, 'value': struct.unpack_from('<' + types[field['type']], raw, field['offset'])[0]}
              for field in abi['pipeline_fields']]
    values = {field['name']: field['value'] for field in fields}
    tokens = values['local.config.tokens']
    assert tokens in (4, 32)
    shared_fixture.TOKENS = tokens
    local_offset = values['local.local_offset']
    gather_offset, local_end = (values['local.layout.' + name] for name in ('gather_offset', 'partial_offset'))
    send = {name.removeprefix('send.config.'): value for name, value in values.items() if name.startswith('send.config.')}
    assert (send['rank'], send['tokens'], send['hidden'], send['topk'], send['chunks'], send['world'],
            send['sendCores'], send['sixPortQps'], send['twoPortQps'], send['syncMode'], send['rotationPolicy']) == (
                rank, tokens, 128, 8, 4, 64, 16, 16, 16, 0, 0)
    assert send['inputOffset'] == 0 and send['partialOffset'] == 2 * tokens * ROW
    local_ready, signal = (values[name] for name in ('localReadyOffset', 'localSignalOffset'))
    assert (local_ready, signal) == (send['workspaceBytes'], send['workspaceBytes'] + 8 * ROW)
    assert local_offset == signal + (8 + 64) * ROW
    assert [value for name, value in values.items() if name.startswith('cores.')] == [8, 0, 8, 16, 24, 8, 32, 16]
    assert gather_offset + 8 * tokens * ROW == local_end
    status_offset = local_offset + local_end
    status = b'\xff' * 1536
    workspace = bytearray(status_offset) + status
    data = route.build_routes(rank, lambda server, local, token, h:
                              shared_fixture.values(server * 8 + local, token)[h], tokens, config.CHUNKS)
    workspace[:len(data['input'])] = data['input']
    for gateway in range(8):
        struct.pack_into('<Q', workspace, local_offset + (3 * 64 + gateway) * ROW, 1)
    images = runtime.runtime_images(abi, rank, len(workspace))
    images.update(shared_fixture.fullmesh(abi, rank, runtime, images))
    images.update({name: image for name, image in data.items() if name in addresses})
    images.update(prefill=prefill, workspace=workspace,
                  output=b'\x00\x00\xc0\x7f' * (tokens * config.HIDDEN))

    status_addr = workspace_address + status_offset
    pointers = {
        'local.workspace': workspace_address + local_offset, 'local.input': workspace_address,
        'local.forward': addresses['forward'], 'local.forward_counts': addresses['counts'],
        'local.ranges': addresses['ranges'], 'local.masks': addresses['masks'],
        'local.status': status_addr, 'local.comm': addresses['commArgs'],
        'local.fullmesh_info': addresses['fullmesh_info'], 'local.registry': addresses['registry'],
        'local.prefill': addresses['prefill'], 'send.commArgs': addresses['commArgs'],
        'send.workspace': workspace_address, 'send.forward': addresses['return_forward'],
        'send.counts': addresses['counts'], 'send.ranges': addresses['ranges'],
        'send.status': status_addr + 512, 'returnStatus': status_addr + 256,
        'serverMask': addresses['server_mask'], 'output': addresses['output'],
        'finalStatus': status_addr + 1024, 'localReturnInfo': addresses['fullmesh_info'],
    }
    assert pointers.keys() <= values.keys()
    for field in fields:
        if field['name'] in pointers:
            field['value'] = pointers[field['name']]
    golden = shared_fixture.make_golden(rank, send, status)
    status_expected = bytearray(status)
    for offset, workers, stride in ROLES.values():
        for worker in range(workers):
            struct.pack_into('<I', status_expected, offset + worker * stride, 0)
    golden['status'] = bytes(status_expected)
    offsets = dict(gather=local_offset + gather_offset, gather_ready=local_offset + (3 * 64 + 96) * ROW,
                   partial=send['partialOffset'], ready=send['readyOffset'], local_ready=local_ready,
                   returned=send['returnOffset'], completed=send['completionOffset'], status=status_offset)
    exports = {}
    for name, offset in offsets.items():
        size = len(golden[name])
        assert name == 'status' or not any(workspace[offset:offset + size]), (rank, name)
        exports[name] = dict(addr=workspace_address + offset, size=size)
    exports['output'] = dict(addr=addresses['output'], size=len(golden['output']))
    memory = [dict(name=name, addr=addresses[name], data=image) for name, image in images.items()]
    guards = []
    for name in ('workspace', 'output'):
        for label, offset in (('before', -ROW), ('after', len(images[name]))):
            guard = dict(name=f'guard_{name}_{label}', addr=addresses[name] + offset, size=ROW, expected_byte=222)
            guards.append(guard)
            memory.append(dict(name=guard['name'], addr=guard['addr'], data=b'\xde' * ROW))
    binding = dict(rank=rank, abi_size=len(raw), fields=fields, images=memory, exports=exports, guards=guards,
                   gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000,
                   stack_base_addr=0x100000, param_base_addr=base+0x80000000, binary_addr=base+0x40000000)
    trace = dict(workspace=workspace_address, return_offset=send['returnOffset'], partial_offset=send['partialOffset'],
                 ready_offset=send['readyOffset'], offset_map=offsets)
    return binding, golden, trace
