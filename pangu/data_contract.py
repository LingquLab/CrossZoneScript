from common import REPO, report, run, ssh_script

commit = run(['git','-C',str(REPO),'rev-parse','HEAD'],'data-contract-head')
report('data-contract',ssh_script('ma-0','remote_data_contract.py','/home/ma-user/work/pangu_sophon_pytorch/ma-0/' + commit,timeout=90))
