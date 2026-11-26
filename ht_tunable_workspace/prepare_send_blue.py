"""Generate the per-rank standalone send arguments and native scheduling plans."""
import argparse
import io
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('probe', type=Path)
parser.add_argument('generator', type=Path)
args = parser.parse_args()

root = Path(__file__).resolve().parent
combinations = [(8, 8, 1), (16, 8, 1), (8, 16, 1), (16, 16, 1), (16, 16, 2), (16, 16, 3)]
bundle = io.BytesIO()
with tempfile.TemporaryDirectory(prefix='ht-send-plan-') as tmp, zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
    plan = Path(tmp) / 'plan.bin'
    for rank in range(64):
        abi = subprocess.check_output([args.probe, str(rank), '32', '128', '6', '1536', '1536', '2026', '-', '64', '8'])
        schema = json.loads(abi)
        assert schema['abi_size'] == 192 and len(schema['fields']) == 32
        archive.writestr(f'{rank}/abi.json', abi)
        for six, two, policy in combinations:
            subprocess.run([args.generator, str(rank), str(six), str(two), str(policy), plan], check=True)
            archive.writestr(f'{rank}/{six}-{two}-{policy}.bin', plan.read_bytes())
(root / 'send-plans.zip').write_bytes(bundle.getvalue())
