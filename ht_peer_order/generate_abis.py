"""Build the shared send ABI plans from the pinned TileXR source."""
import io
import json
import tarfile
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
here = Path(__file__).resolve().parent
revision = '8882b28a4cc586f91ddcea171170bf9e304adc70'
source = 'tools/unitest/simulator/experiments/inter_server_send/sst/abi_probe.cpp'
with tempfile.TemporaryDirectory() as temp:
    temp = Path(temp)
    archived = subprocess.check_output(['git', 'archive', revision, 'include'], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(archived)) as archive:
        archive.extractall(temp, filter='data')
    (temp/'probe.cpp').write_bytes(subprocess.check_output(['git', 'show', f'{revision}:{source}'], cwd=root))
    probe = temp / 'abi'
    subprocess.run(['c++', '-std=c++14', '-O2', '-I'+str(temp/'include'), temp/'probe.cpp', '-o', probe], check=True)
    results = {}
    for tokens in (32, 64):
        rows = tokens * 8
        results[str(tokens)] = [json.loads(subprocess.check_output([probe, str(rank), str(tokens), '256',
            '6', str(rows), str(rows), '2026'])) for rank in range(64)]
    import gzip
    (here/'abis.json.gz').write_bytes(gzip.compress(json.dumps(results, separators=(',', ':')).encode(), mtime=0))
