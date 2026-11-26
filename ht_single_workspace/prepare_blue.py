"""Compile native host layout inputs and package the BS32 SST templates."""
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
spec = importlib.util.spec_from_file_location('single_workspace_fixture', root / 'ht_single_workspace/fixture.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)

parser = argparse.ArgumentParser()
parser.add_argument('tilexr', type=Path)
parser.add_argument('prefill_generator', type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.tilexr / 'tools/unitest/simulator/experiments/inter_server_send/sst'))
import loader
import runtime

with tempfile.TemporaryDirectory(prefix='ht-single-workspace-plans-') as directory:
    temp = Path(directory)
    with zipfile.ZipFile(root / 'ht_parallel_unified/plans-t32.zip') as runtime_abi, zipfile.ZipFile(
            root / 'ht_single_workspace/plans.zip', 'w', zipfile.ZIP_DEFLATED) as plans:
        for rank in range(64):
            abi = json.loads(runtime_abi.read(f'{rank}/abi.json'))
            images = runtime.runtime_images(abi, rank, 2**20)
            mem = fixture.shared_fixture.fullmesh(abi, rank, runtime, images)['fullmesh_mem']
            (temp / 'mem').write_bytes(mem)
            subprocess.run([args.prefill_generator, str(rank), temp / 'mem', temp / 'plan'], check=True)
            raw = (temp / 'plan.args').read_bytes()
            prefill = (temp / 'plan.bin').read_bytes()
            binding, golden, trace = fixture.build_rank(rank, abi, raw, prefill, (None, runtime))
            assert set(binding['exports']) == set(golden) == set(fixture.NAMES)
            packed, _ = loader.pack_arguments(binding['fields'], binding['abi_size'])
            assert len(packed) == 480 and packed[448:] == raw[448:]
            base = trace['workspace']
            for peer in range(8):
                if peer == rank % 8:
                    continue
                peer_base = ((rank & ~7) + peer) * runtime.GM_BYTES + runtime.BASES['workspace']
                for chunk in range(4):
                    first, count, signal, notify, _ = struct.unpack_from('<3Q2I', prefill, 512 + (peer * 4 + chunk) * 32)
                    assert (count, notify, signal) == (8, 1, first + 8 * 64)
                    destination = trace['offset_map']['gather'] + (rank % 8 * 32 + 8 * chunk) * fixture.ROW
                    assert struct.pack('<Q', peer_base + destination) in prefill[first:first + 48]
                    assert struct.pack('<Q', base + 8 * chunk * fixture.ROW) in prefill[first + 48:first + 64]
            for name, data in [('abi.json', runtime_abi.read(f'{rank}/abi.json')), ('args', raw), ('prefill', prefill)]:
                plans.writestr(f'{rank}/{name}', data)
print(hashlib.sha256((root / 'ht_single_workspace/plans.zip').read_bytes()).hexdigest())
