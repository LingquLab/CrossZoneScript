"""Expand the four confirmed 8P kernels to 512KiB per rank."""
import json
from pathlib import Path
import subprocess

from run_profiled_points import ROOT, SSH, run_points

p = subprocess.run(SSH + ['timeout 85 python3 -'],
    input=Path(__file__).with_name('find_prefill_assets.py').read_bytes(), capture_output=True, timeout=100)
if p.returncode:
    raise RuntimeError('Targeted Prefill metadata search failed')
assets = json.loads(p.stdout)
(ROOT / 'prefill-asset-search.json').write_text(json.dumps(assets, indent=2), encoding='utf-8')
print(json.dumps({'prefill_asset_search': assets}, ensure_ascii=True), flush=True)
run_points([('urma_lr8', 8, 16384, 409.179), ('urma_cw', 8, 16384, 410.441),
            ('urma_lr8_p16_s16_local7', 8, 16384, 412.608), ('urma_lr8_p16_s8_local7', 8, 16384, 410.506)],
           campaign='profiled-8p-512k-1', simulation_timeout=1800)
