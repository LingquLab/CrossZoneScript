# Copyright (c) 2026 TileXR Project.
"""Inspect installed model families and source availability for the MMIO fault."""

import hashlib
import json
from pathlib import Path

import yaml

root = Path('/opt/sim/simulator/v310_deployment/config')
result = dict(ok=True,read_only=True,model_families=[],model_source_files=[])
for path in sorted(root.rglob('*.yml')):
    if 'simulator_config' not in path.name or path.stat().st_size > 2000000:
        continue
    config = yaml.safe_load(path.read_text())
    su = config.get('simulator_config',{}).get('su') if isinstance(config,dict) else None
    if su:
        result['model_families'].append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            component=su.get('component'),lsu_level=su.get('params',{}).get('lsu_level'),
            peer_files=[p.name for p in sorted(path.parent.glob('*.yml'))]))
for pattern in ('**/*Lsu*.cpp','**/*lsu*.cpp','**/*Lsu*.cc','**/*ScalarUnit*.cpp','**/*ScalarUnit*.h','**/*Lsu*.h'):
    for path in Path('/opt/sim').glob(pattern):
        if path.is_file():
            result['model_source_files'].append(dict(path=str(path),bytes=path.stat().st_size))
        if len(result['model_source_files'])>=24:
            break
    if len(result['model_source_files'])>=24:
        break
print(json.dumps(result,ensure_ascii=True))
