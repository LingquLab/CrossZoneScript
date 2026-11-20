"""Read case identity and allocation contracts inside the simulator container."""
import json
from pathlib import Path
import sys

case = Path(sys.argv[1])
assert case.is_relative_to('/work') and '..' not in case.parts
scenario = json.loads((case / 'scenario.json').read_text())
provenance = json.loads((case / 'production_provenance.json').read_text())
print(json.dumps({'rank_size': scenario['rank_size'], 'elements_per_peer': scenario['elements_per_peer'],
    'block_dim': scenario['block_dim'], 'stage_profiling': scenario['stage_profiling'],
    'profiling_contract': scenario['profiling_contract'], 'resource_budget': scenario['resource_budget'],
    'kernel_sha256': provenance['kernel_sha256'], 'source_lock_sha256': provenance['source_lock_sha256']}, ensure_ascii=True))
