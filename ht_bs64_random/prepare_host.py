"""Build the small ABI and prefill bundle on blue using the pinned TileXR tree."""
import io
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from routes import CHUNKS, HIDDEN, LOCAL, MAGIC, TOKENS, TOPK, WORLD, build_routes

root = Path(sys.argv[1]).resolve()
directory = Path(__file__).resolve().parent
runner = root / 'tools/unitest/simulator/experiments/inter_server_send/sst'
sys.path.insert(0, str(runner))
import runtime

local = root / 'src/moonep/combine_ht'
inputs, gather, _, _, backward, _ = build_routes()
input_rows, gather_rows = max(map(len, inputs)), max(map(len, gather))
with tempfile.TemporaryDirectory(prefix='ht-bs64-blue-') as temp, io.BytesIO() as packed:
    temp = Path(temp)
    subprocess.run(['c++', '-std=c++14', '-O2', '-I' + str(root / 'include'), '-I' + str(local / 'common'),
                    '-DTILEXR_COMBINE_HT_SST_FP32=1', str(directory / 'prefill.cpp'),
                    str(local / 'host/combine_ht_prefill.cpp'), '-o', str(temp / 'prefill')], check=True)
    subprocess.run(['c++', '-std=c++14', '-O2', '-I' + str(root / 'include'), str(runner / 'abi_probe.cpp'),
                    '-o', str(temp / 'abi')], check=True)
    with zipfile.ZipFile(packed, 'w', zipfile.ZIP_DEFLATED) as archive:
        for rank in range(WORLD):
            cpu_args = [str(rank), str(TOKENS), str(HIDDEN * 2), str(TOPK), str(input_rows),
                        str(input_rows), str(MAGIC)]
            abi = subprocess.check_output([temp / 'abi', *cpu_args])
            images = runtime.runtime_images(json.loads(abi), rank, 1024 * 1024)
            (temp / 'mem').write_bytes(images['native_mem_info'])
            plan = [str(rank), str(input_rows), str(gather_rows)]
            for gateway in range(LOCAL):
                for chunk in range(CHUNKS):
                    rows = backward[rank][gateway, chunk]
                    plan.append(str(len(rows)))
                    plan.extend(str(value) for row in rows for value in row)
            subprocess.run([temp / 'prefill', temp / 'mem', temp / 'args', temp / 'prefill.bin'],
                           input=' '.join(plan).encode(), check=True)
            archive.writestr(f'{rank}/abi.json', abi)
            archive.writestr(f'{rank}/args.bin', (temp / 'args').read_bytes())
            archive.writestr(f'{rank}/prefill.bin', (temp / 'prefill.bin').read_bytes())
    (directory / 'plans.zip').write_bytes(packed.getvalue())
