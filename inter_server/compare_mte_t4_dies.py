"""Diagnose T4 data from both retained Die dumps without replay or writes."""

import json
from pathlib import Path
import subprocess

source = r"""
import collections
import json
import re
from pathlib import Path
import sys

case = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-state-8720cf34')
sys.dont_write_bytecode = True
sys.path.insert(0, str(case / 'adapter'))
from exports import V310_DECODER_SHA256, read_addressed

environment = json.loads((case / 'sst-environment.json').read_text())
assert environment['decoder_library_sha256'] == V310_DECODER_SHA256
layout = json.loads((case / 'export-layout.json').read_text())
checker_log = (case / 'check.log').read_text(errors='replace')
failure = next((key for key, fragment in (
    ('status', 'kernel status failed'),
    ('returned', 'returned payload/untouched slots differ'),
    ('completed', 'readiness differs at slot'),
    ('completed_size', 'completion export size differs'),
) if fragment in checker_log), 'other')
counts = collections.Counter()
statuses = collections.Counter()
examples = []
for row in layout['ranks']:
    rank = row['rank']
    name = f'rank{rank:02d}'
    status = (case / 'actual' / name / 'status.bin').read_bytes()
    assert len(status) == 512
    statuses.update(int.from_bytes(status[i:i + 4], 'little') for i in range(0, len(status), 32))
    for key in ('returned', 'completed'):
        region = row['exports'][key]
        address = region['addr'] - row['sys_va_base_addr']
        size = region['size']
        expected = (case / 'fixture-data' / name / f'golden-{key}.bin').read_bytes()
        got = (case / 'actual' / name / f'{key}.bin').read_bytes()
        assert len(expected) == len(got) == size and size % 512 == 0
        width = 512 if key == 'returned' else 8
        mismatches = [offset for offset in range(0, size, 512)
                      if got[offset:offset + width] != expected[offset:offset + width]]
        counts[key + '_ranks_matching'] += not mismatches
        if not mismatches:
            continue
        raw = [read_addressed(case / 'bin' / f'david_{rank}_die{die}_{key}.bin', address, size)
               for die in (1, 2)]
        for offset in mismatches:
            current = address + offset
            side = ((current >> 9) ^ (current >> 16) ^ (current >> 23)) & 1
            expected_slot = expected[offset:offset + width]
            got_slot = got[offset:offset + width]
            assert got_slot == raw[side][offset:offset + width]
            other_slot = raw[1 - side][offset:offset + width]
            kind = 'missing' if not any(got_slot) else 'unexpected' if not any(expected_slot) else 'different'
            counts[key + '_' + kind] += 1
            counts[key + '_other_die_matches'] += other_slot == expected_slot
            if len(examples) < 12:
                diff = [i for i, (a, b) in enumerate(zip(got_slot, expected_slot)) if a != b]
                examples.append({'rank': rank, 'field': key, 'slot': offset // 512,
                                 'kind': kind, 'other_die_matches': other_slot == expected_slot,
                                 'first_byte': diff[0], 'last_byte': diff[-1]})
if 'split-forward' in case.name:
    source_rank, target_rank, slot, source_offset, target_offset = 18, 10, 26, 0x800, 0x4a400
else:
    source_rank, target_rank, slot, source_offset, target_offset = 38, 30, 28, 0xc00, 0x4a800
row_size = 512
workspace, rank_span = 0x200000000, 0x2000000000
src = source_rank * rank_span + workspace + source_offset
dst = target_rank * rank_span + workspace + target_offset
actual = (case / 'actual' / f'rank{target_rank:02d}' / 'returned.bin').read_bytes()[slot * row_size:(slot + 1) * row_size]
golden = (case / 'fixture-data' / f'rank{target_rank:02d}' / 'golden-returned.bin').read_bytes()[slot * row_size:(slot + 1) * row_size]
source_workspace = (case / 'input_bin' / f'rank{source_rank:02d}' / 'workspace.bin').read_bytes()
source_row = source_workspace[source_offset:source_offset + row_size]
candidates = []
for name in ('input', 'partial'):
    for rank in range(64):
        rows = (case / 'fixture-data' / f'rank{rank:02d}' / (name + '.bin')).read_bytes()
        for row in range(len(rows) // row_size):
            if rows[row * row_size:(row + 1) * row_size] == actual:
                candidates.append((name, rank, row))
trace = collections.Counter()
event_order = []
for rank in (source_rank, target_rank):
    for layer in ('ba', 'dl'):
        path = case / 'OUTPUT' / 'stat_trace' / f'Host{rank}_{layer}_layer_trace.json'
        for match in re.finditer(r'\{[^{}]*"(?:oriAddr_|rdAddr_|wrAddr_)"[^{}]*\}', path.read_text()):
            try:
                args = json.loads(match.group())
            except ValueError:
                continue
            for field in ('oriAddr_', 'rdAddr_', 'wrAddr_'):
                if field not in args:
                    continue
                value = int(str(args[field]), 0)
                for which, address in (('source', src), ('target', dst)):
                    if not address <= value < address + row_size:
                        continue
                    event = str(args.get('busPayloadType_', args.get('name', 'unknown')))[:48]
                    trace[f'{rank}/{layer}/{which}/{event}/{field}'] += 1
                    if field == 'oriAddr_' and len(event_order) < 20:
                        event_order.append([rank, layer, which, event, value - address])
route = {'source_rank': source_rank, 'target_rank': target_rank, 'slot': slot,
         'source_image_matches_golden': source_row == golden,
         'actual_has_one_repeated_bf16_word': len({actual[i:i + 2] for i in range(0, len(actual), 2)}) == 1,
         'actual_source_candidate_total': len(candidates),
         'actual_source_candidates': candidates[:8], 'trace': dict(trace), 'event_order': event_order}
print(json.dumps({'checker_first_failure': failure, 'counts': dict(counts),
                  'status_values': dict(statuses), 'examples': examples, 'route': route}, ensure_ascii=True))
"""
if "metadata" in Path(__file__).stem:
    source = source.replace("t4-sst-mte-state-8720cf34", "t4-sst-mte-split-forward-0d3c8694")
compile(source, "<remote_dies>", "exec")
proc = subprocess.run(
    ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
     "-o", "ConnectTimeout=10", "root@141.61.94.58", "python3", "-"],
    input=source.encode("ascii"),
    capture_output=True,
    timeout=60,
)
if proc.returncode:
    import re
    errors = proc.stderr.decode("utf-8", "replace")
    print(json.dumps({"diagnostic_failure": True, "ssh_exit_code": proc.returncode,
        "errors": re.findall(r"^([A-Za-z]+(?:Error|Exception)):", errors, re.MULTILINE)[-3:]}))
else:
    print(json.dumps(json.loads(proc.stdout), ensure_ascii=True))
