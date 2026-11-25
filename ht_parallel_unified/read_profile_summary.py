"""Return selected timing measurements from the verified Windows Perfetto export."""
import hashlib
import json
from pathlib import Path

from config import T32_PROFILE_CASE

directory = Path('D:/project/tilexr-inter-server-sst') / T32_PROFILE_CASE
data = (directory / 'profile-summary.json').read_bytes()
summary = json.loads(data)
names = (
    'gather_quiet',
    'gateway_wait_gather',
    'gateway_reduce_partial',
    'local_return_wait_ready',
    'local_return_wait_cq',
    'cross_return_wait_ready',
    'cross_return_payload_wait_cq',
    'cross_return_so_wait_cq',
    'final_wait_window',
    'final_reduce_token',
)
assert summary['streams'] == summary['expected_streams'] == 3584
assert not summary['invalid'] and not summary['unknown_markers']
result = {
    'summary_sha256': hashlib.sha256(data).hexdigest(),
    'unknown_markers': summary['unknown_markers'],
    'columns': ['median_us', 'p95_us', 'max_us'],
    'stages': {name: [summary['stages'][name][key] for key in ('median_us', 'p95_us', 'max_us')]
               for name in names},
}
print(json.dumps(result, separators=(',', ':')))
