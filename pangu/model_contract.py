"""Bind per-device occupancy and model prerequisites to the deployed source."""
from common import HERE, REPO, report, run, ssh_script
import npu_status

targets = npu_status.load_config(str(HERE / 'env_list.json'))
occupancy = npu_status.check_targets(targets, 3)
commit = run(['git','-C',str(REPO),'rev-parse','HEAD'],'contract-head')
remote = ssh_script('ma-0','remote_model_contract.py','/home/ma-user/work/pangu_sophon_pytorch/ma-0/' + commit,timeout=150)
report('model-contract', {'occupancy':occupancy,'model_contract':remote})
