"""Validate 16P/32P small-message points before expensive large-message runs."""
import json
from pathlib import Path
import subprocess

from run_profiled_points import ROOT, SSH, run_points

remote = r'''import json,subprocess,os
from pathlib import Path
p=subprocess.run(['timeout','35','find','/tmp','/var/tmp','/root','-maxdepth','4','-type','d','(',
 '-iname','*prefill*','-o','-iname','*matrix*','-o','-iname','*alltoall*',')'],capture_output=True,timeout=40)
paths=[line for line in p.stdout.decode(errors='replace').splitlines() if 'crosszone-tilexr-acc8c509' not in line]
stat=os.statvfs('/root/crosszone-tilexr-acc8c509')
print(json.dumps({'temporary_asset_search_exit_code':p.returncode,'asset_candidates':sorted(paths)[:80],
 'work_disk_free_bytes':stat.f_bavail*stat.f_frsize}))
'''
p = subprocess.run(SSH + ['timeout 55 python3 -'], input=remote.encode(), capture_output=True, timeout=70)
if p.returncode:
    raise RuntimeError('Targeted simulator asset and disk check failed')
assets = json.loads(p.stdout)
(ROOT / 'temporary-asset-search.json').write_text(json.dumps(assets, indent=2), encoding='utf-8')
assert assets['work_disk_free_bytes'] > 4 * 1024 ** 3
print(json.dumps({'asset_and_disk_check': assets}, ensure_ascii=True), flush=True)
run_points([('urma_lr8', 16, 128, 35.820), ('urma_cw', 16, 128, 36.248),
            ('urma_lr8_p16_s16_local7', 16, 128, 36.341), ('urma_lr8_p16_s8_local7', 16, 128, 41.833),
            ('urma_lr8', 32, 128, 54.679), ('urma_cw', 32, 128, 54.336),
            ('urma_lr8_p16_s16_local7', 32, 128, 56.407), ('urma_lr8_p16_s8_local7', 32, 128, 69.669)],
           campaign='profiled-16p-32p-small-1', simulation_timeout=900)
