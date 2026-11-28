from common import REPO, report, run, ssh_script

commit=run(['git','-C',str(REPO),'rev-parse','HEAD'],'tokenizer-head')
report('tokenizer-contract',ssh_script('ma-0','remote_tokenizer_contract.py',
    '/home/ma-user/work/pangu_sophon_pytorch/ma-0/'+commit,timeout=60))
