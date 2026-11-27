"""Compile native host layout inputs and package the tunable SST templates."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import zipfile

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('tunable_workspace_fixture', root / 'ht_tunable_workspace/fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
from sync_layout import chunk_ends, cross_signal_chunks, sync_steps

fixture.chunk_ends = chunk_ends
fixture.cross_signal_chunks = cross_signal_chunks
fixture.sync_steps = sync_steps

parser = argparse.ArgumentParser()
parser.add_argument('tilexr', type=Path)
parser.add_argument('prefill_generator', type=Path)
parser.add_argument('abi_probe', type=Path)
parser.add_argument('tokens', type=int, choices=(4, 32, 1024))
parser.add_argument('--mode', choices=('direct', 'logical', 'physical'))
parser.add_argument('--send-plan-generator', type=Path)
parser.add_argument('--bf16', action='store_true')
args = parser.parse_args()
tokens = args.tokens
if args.bf16 and (tokens != 4 or args.mode is not None):
    parser.error('--bf16 requires the T4 Window case')
hidden = 256 if args.bf16 else 128
row_bytes = 512
chunk_ranges = list(zip([0] + chunk_ends(tokens)[:-1], chunk_ends(tokens)))
if args.mode == 'direct' and args.send_plan_generator is None:
    parser.error('--mode direct requires --send-plan-generator')
pipeline = json.loads(subprocess.check_output([args.abi_probe, 'pipeline']))
sys.path.insert(0, str(args.tilexr / 'tools/unitest/simulator/experiments/inter_server_send/sst'))
import loader

runtime_spec = importlib.util.spec_from_file_location('candidate_runtime', Path(__file__).with_name('runtime.py'))
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)

suffix = '-bf16' if args.bf16 else f'-{args.mode}' if args.mode else ''
bundle_path = root / 'ht_tunable_workspace' / f'plans-t{tokens}{suffix}.zip'
with tempfile.TemporaryDirectory(prefix='ht-single-workspace-plans-') as directory:
    temp = Path(directory)
    with zipfile.ZipFile(bundle_path, 'w', zipfile.ZIP_DEFLATED) as plans:
        for rank in range(64):
            abi = json.loads(subprocess.check_output([args.abi_probe, str(rank), str(tokens), str(hidden),
                                                       '8', str(2*tokens), str(2*tokens), '2026', '-', '64', '8']))
            assert abi['abi_size'] == 192 and len(abi['fields']) == 32
            abi['pipeline_fields'] = pipeline['fields']
            prefill_opts = ['h256'] if args.bf16 else []
            send_plan = None
            if args.mode == 'direct':
                prefill_opts = ['direct']
                subprocess.run([args.send_plan_generator, str(rank), '8', '8', '1', temp / 'send.bin'], check=True)
                send_plan = (temp / 'send.bin').read_bytes()
            elif args.mode is not None:
                prefill_opts = ['reuse', str({'logical': 1, 'physical': 2}[args.mode])]
            images = runtime.runtime_images(abi, rank, 2**20)
            mem = fixture.shared_fixture.fullmesh(abi, rank, runtime, images)['fullmesh_mem']
            (temp / 'mem').write_bytes(mem)
            subprocess.run([args.prefill_generator, str(rank), temp / 'mem', temp / 'plan', str(tokens), *prefill_opts], check=True)
            raw = (temp / 'plan.args').read_bytes()
            prefill = (temp / 'plan.bin').read_bytes()
            binding, golden, trace = fixture.build_rank(rank, abi, raw, prefill, (None, runtime), send_plan, bf16=args.bf16)
            assert set(binding['exports']) == set(golden) == set(fixture.NAMES)
            packed, _ = loader.pack_arguments(binding['fields'], binding['abi_size'])
            assert len(packed) == pipeline['abi_size'] and len(binding['fields']) == 93
            base = trace['workspace']
            for peer in range(8):
                if peer == rank % 8:
                    continue
                peer_base = ((rank & ~7) + peer) * runtime.GM_BYTES + runtime.BASES['workspace']
                for chunk, (token_begin, token_end) in enumerate(chunk_ranges):
                    stage = peer * len(chunk_ranges) + chunk
                    first, count, signal, notify, _ = struct.unpack_from('<3Q2I', prefill, 512 + stage * 32)
                    span = token_end - token_begin
                    assert (count, notify, signal) == (span, 1, first + span * 64)
                    destination = trace['offset_map']['gather'] + (rank % 8 * tokens + token_begin) * row_bytes
                    assert struct.pack('<Q', peer_base + destination) in prefill[first:first + 48]
                    assert struct.pack('<Q', base + token_begin * row_bytes) in prefill[first + 48:first + 64]
            for name, data in [('abi.json', json.dumps(abi).encode()), ('args', raw), ('prefill', prefill)]:
                plans.writestr(f'{rank}/{name}', data)
            if send_plan is not None:
                plans.writestr(f'{rank}/send.bin', send_plan)
print(hashlib.sha256(bundle_path.read_bytes()).hexdigest())
