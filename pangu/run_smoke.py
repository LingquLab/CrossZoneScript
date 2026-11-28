"""Require all eight ma-0 devices to be idle immediately before training."""
import json
import sys

from common import HERE, LOGS, REPO, report, run, ssh_script
import npu_status

targets = [t for t in npu_status.load_config(str(HERE / 'env_list.json')) if t.name == 'ma-0']
occupancy = npu_status.check_targets(targets,1)
report('before-gqa-smoke',occupancy)
assert occupancy['safe_to_use'], 'ma-0 is not confirmed fully idle; training not started'
commit = run(['git','-C',str(REPO),'rev-parse','HEAD'],'smoke-head')
try:
    result = ssh_script('ma-0','remote_smoke.py','/home/ma-user/work/pangu_sophon_pytorch/ma-0/' + commit,
                        (HERE/'runtime_sitecustomize.py').read_text(),timeout=750)
except RuntimeError:
    # Read the captured structured result, never rerun a workload to recover its output.
    path = LOGS / 'ma-0-remote_smoke.stdout'
    if path.is_file() and path.stat().st_size:
        try:
            report('gqa-smoke-failed',json.loads(path.read_text(encoding='utf-8')))
        except json.JSONDecodeError:
            pass
    raise
report('gqa-smoke',result)
