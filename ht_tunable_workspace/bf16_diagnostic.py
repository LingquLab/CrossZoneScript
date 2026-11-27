"""Summarize the retained BS4 BF16 send and return trace."""
from collections import Counter

def integer(value):
    return int(value, 0) if isinstance(value, str) else value


state = json.loads((case / 'run.json').read_text())
report = {key: state[key] for key in ('status', 'error', 'returncode', 'kernel_sha256') if key in state}
sim = case / 'sim.log'
raw = sim.read_bytes() if sim.exists() else b''
report['log_bytes'] = len(raw)
report['log_cqe'] = raw.count(b'WrCQEResp')
report['log_signals'] = dict(Counter(match.decode().lower() for match in
    re.findall(rb'(?i)unsupported|segmentation fault|undefined symbol|fatal|overflow|invalid opcode|assertion', raw)))
native = case / 'native-runner.log'
if native.is_file():
    report['runner'] = [line[:210] for line in native.read_text(errors='replace').splitlines()[-3:]]
layout = json.loads((case / 'fixture-data/trace-offsets.json').read_text())
report['case'] = case.name
for rank in (0, 8):
    endpoint = layout[rank]
    base, off = endpoint['workspace'], endpoint['offset_map']
    other = rank ^ 8
    pack = base + off['returned']
    origin = base + off['partial']
    ready = base + off['ready']
    db = 0x1C00000000 + other * 20 * 64
    sq = 0x1900000000 + (other * 40) * RING_BYTES
    cqe = CQ_BASE + (other * 40) * RING_BYTES
    observed = Counter()
    samples = []
    trace = case / 'OUTPUT/stat_trace' / f'Host{rank}_ba_layer_trace.json'
    if not trace.exists():
        continue
    for args in trace_events(trace, ('WR_REQ', 'WR_DATA', 'RD_REQ', 'RD_REQ_RESP')):
        addr = integer(args['oriAddr_'])
        chip, local = divmod(addr, GM_BYTES)
        action = args['busPayloadType_']
        length = args.get('size_')
        key = None
        if addr == db:
            key = 'db'
        elif chip == rank and sq <= local < sq + 512:
            key = f'sq{(local - sq) // 64}'
        elif chip == rank and cqe <= local < cqe + 512:
            key = f'cq{(local - cqe) // 64}'
        elif pack <= addr < pack + 4 * 8 * 512 and action == 'WR_DATA':
            key = f'return_t{(addr - pack) // (8 * 512)}_s{(addr - pack) // 512 % 8}'
        elif origin <= addr < origin + 8 * 512 and action in ('WR_DATA', 'RD_REQ'):
            key = f'partial{(addr - origin) // 512}'
        elif ready <= addr < ready + 8 * 512 and action == 'WR_DATA':
            key = 'ready'
        if key:
            label = key + ':' + action
            observed[label] += 1
            if key.startswith(('sq', 'cq', 'db')) and len(samples) < 24:
                samples.append([key, action, length])
    report[str(rank)] = {'events': dict(observed), 'len': samples[:14]}
print(json.dumps(report, separators=(',', ':')))
