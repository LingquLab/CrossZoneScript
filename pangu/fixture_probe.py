from common import HERE, REPO, report, run, ssh_script
import npu_status

targets = [t for t in npu_status.load_config(str(HERE/'env_list.json')) if t.name == 'ma-0']
occupancy = npu_status.check_targets(targets,1)
report('before-broadcast-probe',occupancy)
assert occupancy['safe_to_use'], 'ma-0 is not fully idle'
commit = run(['git','-C',str(REPO),'rev-parse','HEAD'],'fixture-head')
report('fixture-probe',ssh_script('ma-0','remote_fixture_probe.py',
    '/home/ma-user/work/pangu_sophon_pytorch/ma-0/'+commit,(HERE/'runtime_sitecustomize.py').read_text(),timeout=190))
