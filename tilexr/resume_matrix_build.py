"""Build under fresh reserved parents after the observed pre-compile failure."""
import json
import os
from pathlib import Path

from build_matrix_kernels import main

prior = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr/reports/matrix-build-1/progress.json'
state = json.loads(prior.read_text(encoding='utf-8'))
assert state['kernel'] == 'urma_lr8' and state['phase'] == 'build' and state['state'] == 'failed'
main(matrix_id='matrix-build-2')
