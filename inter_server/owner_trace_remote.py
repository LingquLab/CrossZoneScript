"""Summarize the owner-to-physical-core mapping in retained native T4 traces."""
import collections
import json
import re
import struct
import sys
from pathlib import Path

CASE = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-native-639f2fb3')
RECORD = re.compile(r'\{[^{}]*"(?:wrAddr_|oriAddr_)"[^{}]*\}')
ADDRESS_RECORD = re.compile(r'\{[^{}]*"(?:wrAddr_|rdAddr_|oriAddr_)"[^{}]*\}')
GM_BYTES = 0x2000000000
DOORBELL_BASES = (0x1C00000000, 0x1D00000000)
LIMIT = 8 * 1024 * 1024


def doorbell_coordinates(value):
    address = int(value, 0) if isinstance(value, str) else int(value)
    local = address % GM_BYTES
    for lane, base in enumerate(DOORBELL_BASES):
        delta = local - base
        if 0 <= delta < 64 * 20 * 64 and delta % 64 == 0:
            peer, owner = divmod(delta // 64, 20)
            if owner < 16:
                return peer, owner, lane
    return None


def trace_mapping(data):
    mapping = collections.defaultdict(set)
    event_names = collections.Counter()
    lanes = set()
    requests = collections.Counter()
    for match in RECORD.finditer(data):
        try:
            args = json.loads(match.group())
            core = int(args['aicoreId_'])
            subcore = int(args['subcoreId_'])
        except (KeyError, TypeError, ValueError):
            continue
        coordinates = set()
        for field in ('wrAddr_', 'oriAddr_'):
            try:
                entry = doorbell_coordinates(args[field])
            except (KeyError, TypeError, ValueError):
                continue
            if entry is not None:
                coordinates.add(entry)
        if coordinates:
            name = str(args.get('busPayloadType_', args.get('name', 'unknown')))
            if re.fullmatch(r'[A-Za-z0-9_ .:-]{1,64}', name):
                event_names[name] += 1
        for _, owner, lane in coordinates:
            mapping[owner].add((core, subcore))
            lanes.add((owner, lane))
        if args.get('busPayloadType_') == 'WR_REQ':
            requests.update(coordinates)
    return mapping, event_names, lanes, requests


def expected_requests(host):
    folder = CASE / 'fixture-data' / f'rank{host:02d}'
    plan = json.loads((folder / 'plan.json').read_text())
    manifest = json.loads((folder.parent / 'manifest.json').read_text())
    assert manifest['tokens'] == 4 and manifest['chunks'] == 4
    ranges = (folder / 'ranges.bin').read_bytes()
    forward = (folder / 'forward.bin').read_bytes()
    so = {}
    for peer, chunk, owner in plan['so']:
        assert chunk == 3 and peer not in so
        so[peer] = owner
    payload = collections.Counter()
    for peer, owner in so.items():
        ordinal = 0
        for chunk in range(4):
            start, end = struct.unpack_from('<ii', ranges, (peer * 4 + chunk) * 8)
            if start == end:
                continue
            next_record, = struct.unpack_from('<i', forward, (peer * 24 + start) * 24)
            assert next_record == end, 'T4 assumes at most one token per peer/chunk'
            payload[peer, owner, int(ordinal % 4 == 3)] += 1
            ordinal += 1
    # Every cross-server QP has one SO and one Drain flush. In T4 each nonempty
    # peer/chunk adds one payload flush on one lane before the window SO.
    expected = {(peer, owner, lane): 2 + payload[peer, owner, lane]
                for peer, owner in so.items() for lane in range(2)}
    return expected, sum(payload.values())


def drain_order(expected, requests):
    by_owner = collections.defaultdict(list)
    results = collections.Counter()
    examples = []
    for (peer, owner, lane), wanted in expected.items():
        by_owner[owner].append((lane, peer, requests[peer, owner, lane] - wanted))
    for owner, rows in by_owner.items():
        ordered = sorted(rows)
        deltas = [delta for _, _, delta in ordered]
        if not any(deltas):
            results['owners_with_all_expected_requests'] += 1
            continue
        missing = next(i for i, delta in enumerate(deltas) if delta)
        if missing and all(delta == -1 for delta in deltas[missing:]):
            results['owners_with_contiguous_one_request_deficit_suffix'] += 1
            examples.append({'owner': owner, 'last_fully_observed_lane_peer': ordered[missing - 1][:2],
                             'first_below_expected_lane_peer': ordered[missing][:2], 'remaining_qps': len(rows) - missing})
        elif missing == 0 and all(delta == -1 for delta in deltas):
            results['owners_with_one_request_deficit_on_all_qps'] += 1
        else:
            results['owners_with_other_patterns'] += 1
    return results, examples


def cq_evidence(data, examples, expected):
    queues = {}
    for each in examples[:2]:
        owner = each['owner']
        for role, field in (('last_fully_observed', 'last_fully_observed_lane_peer'),
                            ('first_below_expected', 'first_below_expected_lane_peer')):
            lane, peer = each[field]
            flat = peer * 40 + 20 * lane + owner
            queues[flat] = {'role': role, 'owner': owner, 'peer': peer, 'lane': lane,
                            'expected_wqes': expected[peer, owner, lane] - 1,
                            'cq_buffer_trace': collections.Counter(), 'sq_buffer_trace': collections.Counter()}
    ring_bytes = 16384 * 64
    for match in ADDRESS_RECORD.finditer(data):
        try:
            args = json.loads(match.group())
        except ValueError:
            continue
        hits = set()
        for field in ('wrAddr_', 'rdAddr_', 'oriAddr_'):
            try:
                value = args[field]
                address = (int(value, 0) if isinstance(value, str) else int(value)) % GM_BYTES
            except (KeyError, TypeError, ValueError):
                continue
            for base, key in ((0x1900000000, 'sq_buffer_trace'), (0x1A00000000, 'cq_buffer_trace')):
                flat, offset = divmod(address - base, ring_bytes)
                if flat in queues and offset < 16 * 64:
                    hits.add((flat, key, offset // 64))
        event = str(args.get('busPayloadType_', 'unknown'))
        if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', event):
            continue
        for flat, key, entry in hits:
            queues[flat][key][f'{event}@{entry}'] += 1
    return list(queues.values())


def timeout_dump_evidence(drain_gaps):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(CASE / 'adapter'))
    from exports import V310_DECODER_SHA256, read_addressed

    environment = json.loads((CASE / 'sst-environment.json').read_text())
    if environment['decoder_library_sha256'] != V310_DECODER_SHA256:
        raise ValueError('Unverified retained Die layout')
    layout = json.loads((CASE / 'export-layout.json').read_text())
    counts = collections.Counter()
    sentinel_owners = set()
    examples = []
    for row in layout['ranks']:
        rank = row['rank']
        fixture = CASE / 'fixture-data' / f'rank{rank:02d}'
        targets = dict(row['exports'])
        targets.update((target['name'], target) for target in row['guards'])
        for name, target in targets.items():
            address = target['addr'] - row['sys_va_base_addr']
            size = target['size']
            assert address % 512 == size % 512 == 0
            paths = [CASE / 'bin' / f'david_{rank}_die{die}_{name}.bin' for die in (1, 2)]
            copies = [read_addressed(path, address, size) for path in paths]
            data = bytearray()
            for offset in range(0, size, 512):
                current = address + offset
                die = ((current >> 9) ^ (current >> 16) ^ (current >> 23)) & 1
                data.extend(copies[die][offset:offset + 512])
            if name == 'status':
                for owner in range(16):
                    code, = struct.unpack_from('<I', data, owner * 32)
                    counts['owner_status_zero' if code == 0 else 'owner_status_sentinel'
                           if code == 0xFFFFFFFF else 'owner_status_other'] += 1
                    if code == 0xFFFFFFFF:
                        sentinel_owners.add((rank, owner))
            elif name in ('returned', 'completed'):
                golden = (fixture / f'golden-{name}.bin').read_bytes()
                assert len(golden) == size
                width = 512 if name == 'returned' else 8
                differs = 0
                for offset in range(0, size, 512):
                    actual, expected = data[offset:offset + width], golden[offset:offset + width]
                    if actual == expected:
                        continue
                    differs += 1
                    kind = 'missing' if not any(actual) else 'unexpected' if not any(expected) else 'different'
                    counts[f'{name}_{kind}_slots'] += 1
                    if len(examples) < 12:
                        examples.append({'rank': rank, 'export': name, 'slot': offset // 512, 'kind': kind})
                counts[f'{name}_ranks_matching'] += int(differs == 0)
            else:
                counts['guard_regions_matching'] += int(data == bytes([target['expected_byte']]) * size)
    return {'counts': dict(counts), 'first_difference_examples': examples,
            'owner_mask_comparison': {'sentinel_and_drain_gap': len(sentinel_owners & drain_gaps),
                                      'sentinel_only': len(sentinel_owners - drain_gaps),
                                      'drain_gap_only': len(drain_gaps - sentinel_owners)}}


def inspect():
    counts = collections.Counter()
    patterns = collections.Counter()
    samples = {}
    request_counts = collections.Counter()
    missing_samples = []
    drain_counts = collections.Counter()
    drain_examples = []
    cq_samples = {}
    drain_gaps = set()
    for host in range(64):
        path = CASE / 'OUTPUT/stat_trace' / f'Host{host}_ba_layer_trace.json'
        if not path.is_file():
            continue
        counts['hosts_sampled'] += 1
        counts['files_prefix_truncated'] += int(path.stat().st_size > LIMIT)
        with path.open('rb') as stream:
            data = stream.read(LIMIT).decode('utf-8', 'replace')
        mapping, names, lanes, requests = trace_mapping(data)
        expected, payload = expected_requests(host)
        request_counts['expected_payload_writes'] += payload
        request_counts['expected_doorbell_requests'] += sum(expected.values())
        request_counts['observed_doorbell_wr_req_records'] += sum(requests.values())
        request_counts['expected_qps'] += len(expected)
        request_counts['observed_expected_qps'] += len(expected.keys() & requests.keys())
        request_counts['unexpected_qps'] += len(requests.keys() - expected.keys())
        order_counts, order_examples = drain_order(expected, requests)
        drain_counts.update(order_counts)
        drain_gaps.update((host, example['owner']) for example in order_examples)
        if host in (0, 63):
            cq_samples[host] = cq_evidence(data, order_examples, expected)
        drain_examples.extend(dict(rank=host, **example) for example in order_examples[:max(0, 8-len(drain_examples))])
        for key, wanted in expected.items():
            got = requests[key]
            request_counts['qps_below_expected'] += int(got < wanted)
            request_counts['qps_above_expected'] += int(got > wanted)
            if got < wanted and len(missing_samples) < 8:
                missing_samples.append({'rank': host, 'peer': key[0], 'owner': key[1], 'lane': key[2],
                                        'expected': wanted, 'observed': got})
        counts['hosts_with_all_16_owners'] += int(len(mapping) == 16)
        counts['hosts_with_all_32_owner_lane_pairs'] += int(len(lanes) == 32)
        counts['hosts_with_one_core_subcore_pair_per_owner'] += int(
            len(mapping) == 16 and all(len(pairs) == 1 for pairs in mapping.values())
        )
        counts['hosts_with_16_distinct_core_subcore_pairs'] += int(
            len(set().union(*mapping.values())) == 16
        )
        key = tuple((owner, tuple(sorted(pairs))) for owner, pairs in sorted(mapping.items()))
        patterns[key] += 1
        if host in (0, 63):
            samples[host] = {'owners': dict(key), 'event_types': dict(names.most_common(8))}
    result = {'counts': dict(counts), 'sq_doorbell_counts': dict(request_counts), 'below_expected_examples': missing_samples,
              'drain_order_counts': dict(drain_counts), 'drain_order_examples': drain_examples,
              'cq_buffer_samples': cq_samples,
              'samples': samples, 'distinct_observed_mappings': len(patterns),
              'dominant_mappings': [{'hosts': n, 'owners': dict(key)} for key, n in patterns.most_common(3)]}
    for name in ('run.json', 'task-result.json'):
        path = CASE / name
        if path.is_file():
            try:
                row = json.loads(path.read_text())
                result[name] = {k: row[k] for k in ('status', 'returncode', 'error', 'runner_exit_code', 'run', 'container_stopped', 'processes') if k in row}
            except ValueError:
                result[name] = {'state': 'updating'}
    if result.get('run.json', {}).get('status') == 'timed_out':
        result['post_timeout_dump'] = timeout_dump_evidence(drain_gaps)
    return result


if __name__ == '__main__':
    print(json.dumps(inspect(), ensure_ascii=True))
