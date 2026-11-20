"""Summarize the owner-to-physical-core mapping in retained native T4 traces."""
import collections
import json
import re
import struct
from pathlib import Path

CASE = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-native-639f2fb3')
RECORD = re.compile(r'\{[^{}]*"(?:wrAddr_|oriAddr_)"[^{}]*\}')
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


def inspect():
    counts = collections.Counter()
    patterns = collections.Counter()
    samples = {}
    request_counts = collections.Counter()
    missing_samples = []
    for host in range(64):
        path = CASE / 'OUTPUT/stat_trace' / f'Host{host}_ba_layer_trace.json'
        if not path.is_file():
            continue
        counts['hosts_sampled'] += 1
        counts['files_prefix_truncated'] += int(path.stat().st_size > LIMIT)
        with path.open('rb') as stream:
            mapping, names, lanes, requests = trace_mapping(stream.read(LIMIT).decode('utf-8', 'replace'))
        expected, payload = expected_requests(host)
        request_counts['expected_payload_writes'] += payload
        request_counts['expected_doorbell_requests'] += sum(expected.values())
        request_counts['observed_doorbell_wr_req_records'] += sum(requests.values())
        request_counts['expected_qps'] += len(expected)
        request_counts['observed_expected_qps'] += len(expected.keys() & requests.keys())
        request_counts['unexpected_qps'] += len(requests.keys() - expected.keys())
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
    return result


if __name__ == '__main__':
    print(json.dumps(inspect(), ensure_ascii=True))
