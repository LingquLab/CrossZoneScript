"""Check the generated 64P DeviceView and new buffer extent for every rank."""

import hashlib
import json
from pathlib import Path
import struct
import sys


path, elements, adapter_sha = sys.argv[1:]
case = Path(path)
scenario = json.loads((case / 'scenario.json').read_text())
validation = json.loads((case / 'validation_summary.json').read_text())
provenance = json.loads((case / 'production_provenance.json').read_text())
contract, budget = scenario['profiling_contract'], scenario['resource_budget']
assert scenario['rank_size'] == 64 and scenario['elements_per_peer'] == int(elements)
assert scenario['stage_profiling'] is True
assert validation['status'] == 'static_generation_passed' and validation['simulated'] is False
assert contract['verdict'] == 'ok' and contract['capacity_per_lane'] == 512
assert contract['expected_max_markers_per_lane'] == 258 and contract['headroom'] == 254
assert budget['verdict'] == 'ok' and budget['limit_bytes'] == 6 * 1024**3 and budget['remaining_bytes'] > 0
assert provenance['adapter_sha256'] == adapter_sha
assert hashlib.sha256((case / 'production_adapter.py').read_bytes()).hexdigest() == adapter_sha
streams = scenario['block_dim']
assert len(scenario['ranks']) == 64 and len({row['rank'] for row in scenario['ranks']}) == 64
for rank in range(64):
    image = (case / 'input_bin' / f'rank{rank}' / 'stage_profile.bin').read_bytes()
    fields = struct.unpack_from('<8I7Q', image)
    assert fields[:8] == (2, 88, 1, streams, 512, 512, 16, 0)
    address = rank * 0x2000000000 + 0x380000000
    timestamps, events, counts, dropped, states = (ptr - address for ptr in fields[8:13])
    assert 88 <= timestamps <= events - streams * 512 * 8
    assert events + streams * 512 * 4 <= counts
    assert counts + streams * 64 <= dropped and dropped + streams * 64 <= states
    assert states + streams * 64 <= len(image)
print(json.dumps({'suite': path, 'rank_size': 64, 'state': 'suite_prepared_not_simulated',
                  'kernel_sha256': provenance['kernel_sha256'], 'profiling_capacity_per_stream': 512,
                  'streams_per_rank': streams, 'remaining_bytes': budget['remaining_bytes']}, ensure_ascii=True))
