"""Check whether retained DL traces expose the local AIV writes needed for pairing."""
from collections import Counter
import re


def dl_coverage(case):
    layout_file = case / 'fixture-data/trace-offsets.json'
    layout = json.loads(layout_file.read_text())
    coverage = Counter()
    field_counts = Counter()
    names = Counter()
    local_timestamps = {}
    for rank in RANKS:
        path = case / 'OUTPUT/stat_trace' / f'Host{rank}_dl_layer_trace.json'
        if not path.is_file():
            continue
        coverage['files'] += 1
        try:
            items = events(path)
        except (ValueError, KeyError, TypeError):
            coverage['malformed'] += 1
            continue
        if items is None:
            coverage['over_limit'] += 1
            continue
        targets = address_plan(case, rank, layout[rank])
        rank_writes = {}
        for event in items:
            if not isinstance(event, dict):
                continue
            fields = event.get('args', {})
            if not isinstance(fields, dict):
                continue
            coverage['events'] += 1
            field_counts.update(fields.keys())
            if isinstance(event.get('ts'), (int, float)):
                coverage['timestamped'] += 1
            name = str(event.get('name', ''))
            if re.fullmatch(r'[a-zA-Z0-9_ -]{1,48}', name):
                names[name] += 1
            op_labels = (name, str(event.get('cat', '')), *(str(value) for key, value in fields.items()
                         if isinstance(value, str) and re.search(r'op|type|engine|pipeline|event|name|source', key, re.I)))
            label = ' '.join(op_labels)
            if re.search(r'\bMTE[_ ]?3\b', label, re.I):
                coverage['mte3_label'] += 1
            for key, value in fields.items():
                if not re.search(r'addr|address', key, re.I):
                    continue
                try:
                    host, offset = divmod(integer(value), RANK_STRIDE)
                except (ValueError, TypeError):
                    continue
                target = targets.get(offset)
                if host not in (rank, 0) or target is None:
                    continue
                kind, index = target
                coverage['address_' + kind] += 1
                timestamp = event.get('ts')
                if not isinstance(timestamp, (int, float)):
                    break
                action = fields.get('busPayloadType_')
                if re.search(r'\bMTE[_ ]?3\b', label, re.I) and re.search(r'WR|STORE|WRITE', label, re.I):
                    action = 'WR_REQ'
                    coverage['mte3_write_' + kind] += 1
                if action in WRITES:
                    rank_writes.setdefault(kind, {}).setdefault(index, {}).setdefault(action, set()).add(timestamp)
                break
        local_timestamps[rank] = rank_writes
    report = dict(coverage=coverage, argument_fields=sorted(field_counts), event_names=names.most_common(12))
    if any(key.startswith(('mte3_write_partial', 'mte3_write_ready')) for key in coverage):
        report['local'] = compare(local_timestamps, 'local_return0', 'partial_local', lambda rank: rank)
        report['cross_arrival'] = compare(local_timestamps, 'cross_return0_arrival', 'partial_cross',
                                          lambda rank: rank ^ 8)
        report['cross_sq'] = compare(local_timestamps, 'cross_return0_sq', 'partial_cross', lambda rank: rank)
    return report
