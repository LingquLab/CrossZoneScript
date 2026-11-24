"""Check retained BS64 SQ and CQ slots without forwarding raw SST traces."""
import json
from pathlib import Path
import subprocess
import sys

here = Path(__file__).resolve().parent
root = here.parent
sys.path[:0] = [str(root / 'ht_t32_e2e'), str(root / 'alltoall')]
from green_auth import askpass_environment
from ssh_transport import HOST

args = json.loads(Path(sys.argv[2]).read_text()) if sys.argv[1:2] == ['--args-file'] else {}
stage = args.get('stage', 'local-send')
assert stage in ('local-send', 'inter-send')
support = root / 'inter_server'
common = (support / 'owner_trace_remote.py').read_text().split('def doorbell_coordinates(', 1)[0]
reader = 'def read_observed(' + (support / 'diagnose_t96_sq_remote.py').read_text().split(
    'def read_observed(', 1)[1].split('def diagnose_t96(', 1)[0]
source = '\n'.join((common, reader, (here / 'routes.py').read_text(), f'STAGE = {stage!r}', r'''
import collections, json, re, time
from pathlib import Path
root = Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native')
case = root / ('ht-bs64-random-20261124-' + STAGE)

def local_groups(rank, backward):
    expected = {}
    for peer in range(rank // LOCAL * LOCAL, (rank // LOCAL + 1) * LOCAL):
        if peer == rank:
            continue
        qp = peer % LOCAL
        ring = peer * 40 + (qp % 2) * 20 + qp // 2
        head = 0
        for chunk in range(CHUNKS):
            rows = len(backward[rank][qp, chunk])
            if rows:
                expected.setdefault(ring, []).append((head, rows, 'payload'))
                expected[ring].append((head + rows, 1, 'so'))
                head += rows + 1
    return expected

def inter_groups(rank, returns):
    per_peer = collections.defaultdict(lambda: [0] * CHUNKS)
    for peer, token, _, _ in returns[rank]:
        if peer // LOCAL != rank // LOCAL:
            per_peer[peer][token // 16] += 1
    expected = {}
    for peer in range(WORLD):
        if peer // LOCAL == rank // LOCAL:
            continue
        group = (peer // LOCAL + 4 - rank // LOCAL % 4) % 4
        pos = 0 if group == 0 else 8 + (group - 1) * 16 + peer // 32 * 8
        pos += (peer % LOCAL + LOCAL - rank % LOCAL) % LOCAL
        owner = (pos + 8) % 16 if pos < 48 else (peer % LOCAL + LOCAL - rank % LOCAL) % LOCAL
        ordinal, head = 0, [0, 0]
        for rows in per_peer[peer]:
            batches = [0, 0]
            for _ in range(rows):
                batches[ordinal % 4 == 3] += 1
                ordinal += 1
            for lane, n in enumerate(batches):
                if n:
                    ring = peer * 40 + lane * 20 + owner
                    expected.setdefault(ring, []).append((head[lane], n, 'payload'))
                    head[lane] += n
        for lane, start in enumerate(head):
            expected.setdefault(peer * 40 + lane * 20 + owner, []).append((start, 1, 'so'))
    return expected

_, _, _, returns, backward, _ = build_routes()
counts, first = collections.Counter(), {}
for rank in range(WORLD):
    path = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
    if not path.is_file():
        continue
    expected = local_groups(rank, backward) if STAGE == 'local-send' else inter_groups(rank, returns)
    observed, truncated = read_observed(path, rank)
    counts['hosts'] += 1
    counts['truncated'] += bool(truncated)
    for ring, groups in expected.items():
        for start, length, kind in groups:
            band = 'cross' if start // 8 != (start + length - 1) // 8 else 'within'
            counts[kind + '_expected'] += length
            for name, event in (('sq', 'RD_REQ'), ('cq', 'WR_REQ')):
                slots = observed[event].get(ring, set())
                missing = sorted(set(range(start, start + length)) - slots)
                counts[kind + '_' + band + '_' + name + '_missing'] += len(missing)
                if missing:
                    first.setdefault(kind + '_' + name, [rank, ring, missing[:8]])
result = {'stage': STAGE, 'counts': counts, 'first': first}
run = case / 'run.json'
if run.is_file():
    spec = json.loads(run.read_text())
    result['run'] = {k: spec[k] for k in ('status', 'returncode', 'timeout_seconds') if k in spec}
log = case / 'sim.log'
if log.is_file():
    raw = log.read_bytes()
    result['cqe'] = raw.count(b'WrCQEResp')
    result['log_silence_seconds'] = round(time.time() - log.stat().st_mtime)
    result['completed'] = b'Simulation is complete' in raw
print(json.dumps(result, separators=(',', ':')))
'''))
if args.get('guarded_local') is True:
    source = source.replace('ht-bs64-random-20261124-', 'ht-bs64-random-20261124-guarded-')
compile(source, '<ht-bs64-trace>', 'exec')
ssh = ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10', '-o', 'BatchMode=no',
       '-o', 'NumberOfPasswordPrompts=1', '-o', 'PreferredAuthentications=password', '-o',
       'PubkeyAuthentication=no', HOST, 'python3', '-']
with askpass_environment() as environment:
    proc = subprocess.run(ssh, input=source.encode(), env=environment, capture_output=True, timeout=170)
if proc.returncode:
    print(json.dumps(dict(ssh_exit=proc.returncode, stderr_bytes=len(proc.stderr))))
else:
    print(json.dumps(json.loads(proc.stdout), separators=(',', ':')))
