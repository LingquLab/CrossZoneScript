"""Count writes to the payload QPs' CQ slots in baseline and probe traces."""
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
sys.path[:0] = [str(root / 'ht_t32_e2e'), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

support = root / 'inter_server'
common = (support / 'owner_trace_remote.py').read_text().split('def doorbell_coordinates(', 1)[0]
reader = 'def read_observed(' + (support / 'diagnose_t96_sq_remote.py').read_text().split(
    'def read_observed(', 1)[1].split('def diagnose_t96(', 1)[0]
source = '\n'.join((common, reader, r'''
from collections import Counter
from pathlib import Path
import json

root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
reports = {}
for case in ('ht-four-stage-8882b28a-inter-send-mte', 'ht-t4-inter-send-sparse-cqe-50066aab'):
    histogram = Counter()
    samples = {}
    truncated_hosts = 0
    for rank in (0, 7, 8, 15, 32, 39, 48, 63):
        path = root / case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
        if not path.is_file():
            continue
        observed, truncated = read_observed(path, rank)
        truncated_hosts += truncated
        peer = rank ^ 8
        rank_server, peer_server = rank // 8, peer // 8
        group = (peer_server + 4 - rank_server % 4) % 4
        position = 0 if group == 0 else 8 + (group - 1) * 16 + peer_server // 4 * 8
        owner = 0 if position >= 48 else (position + 8) % 16
        slots = [sorted(observed['WR_REQ'].get(peer * 40 + lane * 20 + owner, set())) for lane in range(2)]
        histogram[f'{len(slots[0])}/{len(slots[1])}'] += 1
        if len(samples) < 2:
            samples[str(rank)] = slots
    reports[case] = dict(histogram=dict(histogram), sampled=sum(histogram.values()),
                         truncated_hosts=truncated_hosts, samples=samples)
print(json.dumps(reports, separators=(',', ':')))
'''))
compile(source, '<ht-t4-sparse-cqe-trace>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
       '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=100)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
