"""Build the pinned FP32 standalone sender workload and its CQ expectations."""
import copy
import hashlib
import struct

SEND_ENTRY = 'tilexr_ep_ht_inter_server_send_kernel'
SEND_CORES = 8
SEED = 20261124
TOKENS = 32
POLICIES = {'ServerStaggered': 1, 'RankOffsetRing': 2, 'CustomOrder': 3}
PLAN_ADDRESS = 0x409000000
OUTPUT_ADDRESS = 0x406000000


def plan_bindings(rank, plan, six, two, runtime):
    assert len(plan) == 3136
    assert struct.unpack_from('<I', plan)[0] == 1
    bindings = {}
    for owner in range(SEND_CORES):
        for parity in range(2):
            parity_index = 2 * owner + parity
            count = plan[388 + parity_index]
            assert count == 7
            begin = 436 + 56 * parity_index
            for peer in plan[begin:begin + count]:
                assert peer // 8 != rank // 8 and peer < 64
                assert plan[4 + peer * 2 + parity] == owner
                first = 132 + 4 * peer + parity * 2
                qp = plan[first:first + 2]
                assert qp[0] in range(owner * six // 8, (owner + 1) * six // 8)
                assert qp[1] in range(six + owner * two // 8, six + (owner + 1) * two // 8)
                physical = tuple(runtime.physical_qp(index, six, two) for index in qp)
                if parity:
                    assert bindings[peer] == physical
                else:
                    assert peer not in bindings
                    bindings[peer] = physical
    assert len(bindings) == 56
    return bindings


def sender_config(abi):
    return {field['name'].removeprefix('config.'): field['value'] for field in abi['fields']
            if field['name'].startswith('config.')}


def build_send_fixture(abis, plans, six, two, policy, runtime, loader):
    abis = copy.deepcopy(abis)
    addresses = runtime.BASES | {'plan': PLAN_ADDRESS, 'output': OUTPUT_ADDRESS}
    all_queue_slots = []
    for rank, abi in enumerate(abis):
        assert abi['abi_size'] == 192 and len(abi['fields']) == 32
        overrides = {'config.planAddress': rank * runtime.GM_BYTES + PLAN_ADDRESS,
                     'config.sendCores': SEND_CORES, 'config.sixPortQps': six, 'config.twoPortQps': two,
                     'config.rotationPolicy': POLICIES[policy]}
        for field in abi['fields']:
            if field['name'] in overrides:
                field['value'] = overrides[field['name']]
        config = sender_config(abi)
        # The probe computes the offsets with default owner count 16.
        assert config['workspaceBytes'] == config['signalOffset'] + 16 * ROW_BYTES
        next(field for field in abi['fields'] if field['name'] == 'config.workspaceBytes')['value'] = (
            config['signalOffset'] + SEND_CORES * ROW_BYTES)
        all_queue_slots.append(plan_bindings(rank, plans[rank], six, two, runtime))

    images, returned, routes, route_sha, lane_rows = generate_inputs(TOKENS, SEED, abis, runtime)
    bindings, golden, cq_slots = [], [], []
    for rank, abi in enumerate(abis):
        base = rank * runtime.GM_BYTES
        locations = {name: base + address for name, address in addresses.items()}
        fields = abi['fields']
        for field in fields:
            if field['name'] in locations:
                field['value'] = locations[field['name']]
        packed, _ = loader.pack_arguments(fields, 192)
        assert len(packed) == 192
        config = sender_config(abi)
        memory = images[rank]
        memory.update(plan=plans[rank], status=(b'\xff' * 4 + b'\xa5' * 28) * SEND_CORES)
        expected = dict(returned=bytes(returned[rank]), output=bytes(512), status=(bytes(4) + b'\xa5' * 28) * SEND_CORES)
        completed = bytearray(config['returnOffset'] - config['completionOffset'])
        guards = []
        for name in ('workspace', 'output'):
            for label, offset in (('before', -ROW_BYTES), ('after', len(memory[name]))):
                guards.append(dict(name=f'guard_{name}_{label}', addr=locations[name] + offset,
                                   size=ROW_BYTES, expected_byte=222))
        for peer, physical in all_queue_slots[rank].items():
            for lane, slot in enumerate(physical):
                peer_count = struct.unpack_from('<i', memory['counts'], peer * 4)[0]
                payload_count = peer_count - peer_count // 4 if lane == 0 else peer_count // 4
                cq_slots.append([rank, peer, slot, payload_count + 1])
                so_index = (peer * 4 + 3) * 2 + lane
                struct.pack_into('<q', completed, so_index * ROW_BYTES, MAGIC)
        expected['completed'] = bytes(completed)
        golden.append(expected)
        exports = {name: dict(addr=locations[name], size=len(expected[name])) for name in ('output', 'status')}
        for name, offset in (('returned', config['returnOffset']), ('completed', config['completionOffset'])):
            exports[name] = dict(addr=locations['workspace'] + offset, size=len(expected[name]))
        binding_images = [dict(name=name, addr=locations[name], data=data) for name, data in memory.items()]
        binding_images += [dict(name=guard['name'], addr=guard['addr'], data=b'\xde' * ROW_BYTES) for guard in guards]
        bindings.append(dict(rank=rank, abi_size=192, fields=fields, gm_size=runtime.GM_BYTES,
                             sys_va_base_addr=base, ub_base_addr=0x80000, stack_base_addr=0x100000,
                             param_base_addr=base+0x80000000, binary_addr=base+0x40000000, images=binding_images,
                             exports=exports, guards=guards))
    assert sum(row[3] for row in cq_slots) == sum(lane_rows) + 7168
    return bindings, golden, routes, dict(route_sha256=route_sha, payload_by_lane=lane_rows, expected_cq=cq_slots,
                                          plans_sha256=hashlib.sha256(b''.join(plans)).hexdigest())
