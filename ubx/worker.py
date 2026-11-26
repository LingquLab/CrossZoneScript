import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def occupancy():
    def probe(i):
        try:
            p = subprocess.run(['npu-smi', 'info', '-t', 'proc-mem', '-i', str(i)], capture_output=True, text=True, timeout=12)
            return p.returncode == 0 and 'No process in device.' in p.stdout and 'Process id' not in p.stdout
        except Exception:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(probe, range(16)))


def main(c):
    if c['action'] == 'check':
        cards = occupancy()
        return {'ready': all(cards), 'unavailable': [i for i, free in enumerate(cards) if not free]}
    root = Path(c['root'])
    if c['action'] == 'gate':
        case = root / c['run_id']
        if c.get('signal') and case.is_dir():
            (case / c['signal']).touch()
        return {'ready': (case / 'ready').is_file()}
    expected = {'off/bin/ubx_full_intra': '999a9814dfdec6b05754c83677cfb9fbc62dae47a3c64a0a2a64da5b943ef404', 'off/lib/libtile-comm.so': 'afdfda792b1fd53ec445702367ed5dc8c2d0620afb3d55814aa40452db95c508'}
    for rel, digest in expected.items():
        if hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest:
            return {'status': 'BLOCKED', 'reason': 'package_hash_mismatch'}
    if not all(occupancy()):
        return {'status': 'BUSY', 'launched': False}
    case = root / c['run_id']
    case.mkdir(exist_ok=False)
    if c.get('gated'):
        (case / 'ready').touch()
        gate_deadline = time.monotonic() + 90
        while not (case / 'go').is_file():
            if (case / 'abort').is_file() or time.monotonic() > gate_deadline:
                return {'status': 'ABORTED', 'launched': False}
            time.sleep(0.2)
    raw = subprocess.check_output(['bash', '-lc', 'source /usr/local/Ascend/cann/set_env.sh >/dev/null 2>&1\nenv -0'])
    env = dict(x.decode().split('=', 1) for x in raw.split(b'\0') if b'=' in x)
    values = {'RANK_SIZE': 32, 'TILEXR_COMM_ID': '90.90.93.84:' + str(c['port']), 'TILEXR_ENABLE_IPC': 1, 'TILEXR_IPC_SCOPE': 'local', 'TILEXR_ENABLE_RDMA': 1, 'TILEXR_ENABLE_UDMA': 0, 'TILEXR_ENABLE_SDMA': 0, 'TILEXR_RDMA_MODE': 'aiv', 'TILEXR_RDMA_DMA_MODE': 'indep_ub', 'TILEXR_RDMA_DB_MODE': 'hard', 'TILEXR_RDMA_GID_INDEX': 1, 'TILEXR_RDMA_MTU': 4096, 'TILEXR_RDMA_TIMEOUT_MS': 30000, 'TILEXR_UBX_BS': c['bs'], 'TILEXR_UBX_EXPERTS': 384, 'TILEXR_UBX_ROUNDS': c['rounds'], 'TILEXR_UBX_CARDS_PER_SERVER': 16, 'TILEXR_UBX_SEND_CORES': 16, 'TILEXR_UBX_RECV_CORES': 16, 'TILEXR_UBX_RELAY_CORES': 16, 'TILEXR_UBX_CORES': 56, 'TILEXR_UBX_CAPTURE': 0, 'TILEXR_UBX_PACKET_CAPTURE': 0, 'TILEXR_UBX_REUSE': 0, 'TILEXR_UBX_INTRA_ONLY': 0, 'TILEXR_UBX_CLOCK_START': 1, 'TILEXR_UBX_ROUTES_FILE': root / ('routes-bs%d.bin' % c['bs']), 'TILEXR_UBX_WARMUP_ROUNDS': c['warmup'], 'TILEXR_UBX_EXPECT_COPIES': 0, 'TILEXR_UBX_MAX_TOKENS': c['mt']}
    env.update({k: str(v) for k, v in values.items()})
    env['LD_LIBRARY_PATH'] = str(root / 'off/lib') + ':' + env.get('LD_LIBRARY_PATH', '')
    jobs = []
    handles = []
    timeout = False
    network = Path('/proc/sys/net/ipv4/conf/all/accept_local')
    original_network = network.read_text().strip()
    (case / 'network-before.json').write_text(json.dumps({'accept_local': original_network}))
    try:
        network.write_text('1\n')
        if network.read_text().strip() != '1':
            raise RuntimeError('Network setting verification failed')
        for device in range(16):
            rank = c['node'] * 16 + device
            d = case / ('rank-%d' % rank)
            d.mkdir()
            (d / 'records').mkdir()
            child = env.copy()
            child.update(RANK=str(rank), DEVICE_ID=str(device), TILEXR_RDMA_DEVICE='hrn5_' + str(device // 2), TILEXR_RDMA_PORT='1')
            out, err = open(d / 'stdout.log', 'wb'), open(d / 'stderr.log', 'wb')
            handles.extend([out, err])
            p = subprocess.Popen([str(root / 'off/bin/ubx_full_intra'), 'fp8e4m3', str(d / 'records'), c['capture']], env=child, stdout=out, stderr=err, start_new_session=True)
            jobs.append((rank, p, d))
        deadline = time.monotonic() + c['timeout']
        while any(p.poll() is None for _, p, _ in jobs):
            if (case / 'abort').is_file():
                break
            if any(p.poll() not in (None, 0) for _, p, _ in jobs):
                break
            if time.monotonic() > deadline:
                timeout = True
                break
            time.sleep(1)
    finally:
        try:
            for _, p, _ in jobs:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            for _, p, _ in jobs:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(timeout=5)
            for f in handles:
                f.close()
        finally:
            network.write_text(original_network + '\n')
            restored = network.read_text().strip() == original_network
            (case / 'network-restored.json').write_text(json.dumps({'restored': restored}))
            if not restored:
                raise RuntimeError('Network restoration failed')
    rows = []
    for rank, p, d in jobs:
        summaries = [json.loads(f.read_text()) for f in (d / 'records').glob('*.summary.json')]
        valid = len(summaries) == c['rounds'] and sorted(s.get('round', -1) for s in summaries) == list(range(c['rounds']))
        for s in summaries:
            required = {'mode': 'full', 'max_tokens': c['mt'], 'experts': 384, 'batch': c['bs'], 'rank_size': 32, 'total_cores': 56, 'send_cores': 16, 'recv_cores': 16, 'relay_cores': 16, 'intra_core_count': 8, 'device_barrier_status': 0, 'dtype': 'fp8e4m3'}
            valid = valid and all(s.get(k) == v for k, v in required.items()) and bool(s.get('correctness')) and bool(s.get('clock_start')) and bool(s.get('call_includes_sleep'))
            valid = valid and all(s.get(k, 0) > 0 for k in ['output_rows', 'rdma_data_bytes', 'data_wqes', 'mem_records']) and bool(s.get('warmup')) == (s.get('round', -1) < c['warmup'])
        row = {'rank': rank, 'exit': p.returncode, 'rounds': len(summaries), 'correct': bool(valid)}
        if not valid:
            row['summary_fields'] = sorted(set(k for s in summaries for k in s))[:80]
        rows.append(row)
    result = {'status': 'PASS' if len(rows) == 16 and all(r['correct'] and r['exit'] == 0 for r in rows) and not timeout else 'FAIL', 'timed_out': timeout, 'ranks': rows, 'profiling': False, 'msprof': False, 'network_accept_local': Path('/proc/sys/net/ipv4/conf/all/accept_local').read_text().strip()}
    (case / 'result.json').write_text(json.dumps(result))
    return result


try:
    result = main(json.loads(sys.argv[1]))
except Exception as e:
    result = {'status': 'ERROR', 'error_type': type(e).__name__}
print(json.dumps(result))
