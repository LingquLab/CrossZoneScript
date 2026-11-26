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

parser = argparse.ArgumentParser()
parser.add_argument('tilexr', type=Path)
parser.add_argument('prefill_generator', type=Path)
parser.add_argument('abi_probe', type=Path)
parser.add_argument('tokens', type=int, choices=(4, 32))
args = parser.parse_args()
tokens = args.tokens
pipeline = json.loads(subprocess.check_output([args.abi_probe, 'pipeline']))
sys.path.insert(0, str(args.tilexr / 'tools/unitest/simulator/experiments/inter_server_send/sst'))
import loader
import runtime

with tempfile.TemporaryDirectory(prefix='ht-single-workspace-plans-') as directory:
    temp = Path(directory)
    with zipfile.ZipFile(root / f'ht_tunable_workspace/plans-t{tokens}.zip', 'w', zipfile.ZIP_DEFLATED) as plans:
        for rank in range(64):
            abi = json.loads(subprocess.check_output([args.abi_probe, str(rank), str(tokens), '128',
                                                       '8', str(2*tokens), str(2*tokens), '2026', '-', '64', '8']))
            assert abi['abi_size'] == 192 and len(abi['fields']) == 32
            abi['pipeline_fields'] = pipeline['fields']
            images = runtime.runtime_images(abi, rank, 2**20)
            mem = fixture.shared_fixture.fullmesh(abi, rank, runtime, images)['fullmesh_mem']
            (temp / 'mem').write_bytes(mem)
            subprocess.run([args.prefill_generator, str(rank), temp / 'mem', temp / 'plan', str(tokens)], check=True)
            raw = (temp / 'plan.args').read_bytes()
            prefill = (temp / 'plan.bin').read_bytes()
            binding, golden, trace = fixture.build_rank(rank, abi, raw, prefill, (None, runtime))
            assert set(binding['exports']) == set(golden) == set(fixture.NAMES)
            packed, _ = loader.pack_arguments(binding['fields'], binding['abi_size'])
            assert len(packed) == pipeline['abi_size'] and len(binding['fields']) == 93
            base = trace['workspace']
            for peer in range(8):
                if peer == rank % 8:
                    continue
                peer_base = ((rank & ~7) + peer) * runtime.GM_BYTES + runtime.BASES['workspace']
                for chunk in range(4):
                    first, count, signal, notify, _ = struct.unpack_from('<3Q2I', prefill, 512 + (peer * 4 + chunk) * 32)
                    span = tokens // 4
                    assert (count, notify, signal) == (span, 1, first + span * 64)
                    destination = trace['offset_map']['gather'] + (rank % 8 * tokens + span * chunk) * fixture.ROW
                    assert struct.pack('<Q', peer_base + destination) in prefill[first:first + 48]
                    assert struct.pack('<Q', base + span * chunk * fixture.ROW) in prefill[first + 48:first + 64]
            for name, data in [('abi.json', json.dumps(abi).encode()), ('args', raw), ('prefill', prefill)]:
                plans.writestr(f'{rank}/{name}', data)
print(hashlib.sha256((root / f'ht_tunable_workspace/plans-t{tokens}.zip').read_bytes()).hexdigest())
