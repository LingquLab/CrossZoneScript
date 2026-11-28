from common import HERE, report, ssh_script
import npu_status

targets=[t for t in npu_status.load_config(str(HERE/'env_list.json')) if t.name=='ma-0']
occupancy=npu_status.check_targets(targets,1)
report('recovery-occupancy',occupancy)
report('recovery-artifacts',ssh_script('ma-0','remote_recover.py',timeout=80))
