# Copyright (c) 2026 TileXR Project.
"""Inspect installed scalar/MMIO model parameters without running a workload."""

import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

sys.path.insert(0, '/work')
import sst

snapshot = json.loads(Path('/work/environment.snapshot.json').read_text())
case = Path('/work/resume-7de1/doorbell-word-a1/send-only')
result = dict(ok=True, read_only=True, model_configurations=[], component_information={})
for name in ('v310_config/davidV100_simulator_config.yml', 'UBusSim_config/UBusSim_simulator_config.yml'):
    generated = yaml.safe_load((case / name).read_text())
    original = yaml.safe_load(Path(snapshot['resources'][name]['path']).read_text())
    row = dict(name=name, generated_sha256=sst.checksum(case / name))
    if name.startswith('v310'):
        row['su'] = generated['simulator_config']['su']
        row['installed_su'] = original['simulator_config']['su']
        row['architecture'] = {k: v for k, v in generated['architecture_config'].items()
            if isinstance(v, (int, float, bool, str)) and re.search(r'addr|size|num|urma|cache|lsu', k, re.I)}
    else:
        row['simulator_info'] = {k: v for k, v in generated['simulator_info'].items()
            if isinstance(v, (int, float, bool, str))}
        row['ba'] = {k: v for k, v in generated.get('simulator_config', {}).get('ba_layer', {}).get('params', {}).items()
            if isinstance(v, (int, float, bool, str)) and re.search(r'cache|urma|addr|flag|mem', k, re.I)}
    result['model_configurations'].append(row)
tool = Path(snapshot['settings']['sst_bin']).parent / 'sst-info'
if not tool.is_file():
    tool = Path('/opt/sim/local/sstcore/bin/sst-info')
if tool.is_file():
    command = sst.shell_command(snapshot['settings'], 'sst', [tool, 'V310.ScalarUnitL1'])
    proc = subprocess.run(command, capture_output=True, text=True, timeout=35)
    lines = proc.stdout.splitlines()
    relevant = [line[:450] for line in lines if re.search(r'cache|lsu|st.dev|byte|addr|bypass|device|mmio', line, re.I)]
    result['component_information'] = dict(exit_code=proc.returncode, output_bytes=len(proc.stdout),
        relevant_lines=relevant[:20], diagnostic=proc.stderr[:350] if proc.returncode else '')
print(json.dumps(result, ensure_ascii=True))
