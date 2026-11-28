import concurrent.futures

from common import ALIASES, HERE, REPO, report, run, ssh_script
import npu_status

commit=run(['git','-C',str(REPO),'rev-parse','HEAD'],'finalize-head')
success='/home/ma-user/work/pangu_validation/ma-0/8da52234-c88f-451d-95a1-fa33a978690b/gqa-smoke-20261128-204425'
def finalize(alias):
    return ssh_script(alias,'remote_finalize.py',alias,commit,success,timeout=180)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    profiles=list(pool.map(finalize,ALIASES))
targets=[t for t in npu_status.load_config(str(HERE/'env_list.json')) if t.name=='ma-0']
occupancy=npu_status.check_targets(targets,1)
report('final-deployment',{'servers':profiles,'post_test_occupancy':occupancy})
assert occupancy['safe_to_use'],'Post-test ma-0 devices are not all confirmed idle'
