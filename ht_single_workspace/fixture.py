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
TOKENS = 32
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
    local_offset, = struct.unpack_from('<Q', raw, 248)
    local_layout = struct.unpack_from('<6I4Q', raw, 168)
    gather_offset, local_end = local_layout[6:8]
    config_values = struct.unpack_from('<10I8Qq', raw, 304)
    assert config_values[:10] == (rank, 32, 128, 8, 64, 64, 256, 4, 8, 64)
    names = ('inputOffset', 'partialOffset', 'readyOffset', 'completionOffset',
             'returnOffset', 'signalOffset', 'workspaceBytes')
    send = dict(zip(names, config_values[10:17], strict=True))
    assert send['inputOffset'] == 0 and send['partialOffset'] == 2 * TOKENS * ROW
    local_ready, signal, qp_base, qp_stride, finals, pipeline = struct.unpack_from('<2Q4I', raw, 448)
    assert (local_ready, signal, qp_base, qp_stride, finals, pipeline) == (
        send['workspaceBytes'], send['workspaceBytes'] + 8 * ROW, 0, 1, 16, 1)
    assert local_offset == signal + (8 + 64) * ROW
    assert gather_offset + 8 * TOKENS * ROW == local_end
    status_offset = local_offset + local_end
    status = b'\xff' * 1536
    workspace = bytearray(status_offset) + status
    data = route.build_routes(rank, lambda server, local, token, h:
                              shared_fixture.values(server * 8 + local, token)[h], TOKENS, config.CHUNKS)
    workspace[:len(data['input'])] = data['input']
    for gateway in range(8):
        struct.pack_into('<Q', workspace, local_offset + (3 * 64 + gateway) * ROW, 1)
    images = runtime.runtime_images(abi, rank, len(workspace))
    images.update(shared_fixture.fullmesh(abi, rank, runtime, images))
    images.update({name: image for name, image in data.items() if name in addresses})
    images.update(prefill=prefill, workspace=workspace,
                  output=b'\x00\x00\xc0\x7f' * (TOKENS * config.HIDDEN))

    args = bytearray(raw)
    status_addr = workspace_address + status_offset
    struct.pack_into('<14Q', args, 0, workspace_address + local_offset, workspace_address,
                     addresses['forward'], addresses['counts'], addresses['ranges'], addresses['masks'],
                     0, 0, 0, status_addr, 0, addresses['commArgs'], addresses['fullmesh_info'],
                     addresses['registry'])
    struct.pack_into('<Q', args, 240, addresses['prefill'])
    struct.pack_into('<6Q', args, 256, addresses['commArgs'], workspace_address, addresses['return_forward'],
                     addresses['counts'], addresses['ranges'], status_addr + 512)
    struct.pack_into('<4Q', args, 416, status_addr + 256, addresses['server_mask'], addresses['output'],
                     status_addr + 1024)
    fields = [dict(name=f'arg_{offset}', type='uint64_t', offset=offset, value=value)
              for offset, (value,) in zip(range(0, 480, 8), struct.iter_unpack('<Q', args), strict=True)]

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
    binding = dict(rank=rank, abi_size=len(args), fields=fields, images=memory, exports=exports, guards=guards,
                   gm_size=runtime.GM_BYTES, sys_va_base_addr=base, ub_base_addr=0x80000,
                   stack_base_addr=0x100000, param_base_addr=base+0x80000000, binary_addr=base+0x40000000)
    trace = dict(workspace=workspace_address, return_offset=send['returnOffset'], partial_offset=send['partialOffset'],
                 ready_offset=send['readyOffset'], offset_map=offsets)
    return binding, golden, trace
