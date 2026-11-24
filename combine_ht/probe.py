"""Run a bounded diagnostic in this session's existing container."""
import argparse,json,os,shlex,subprocess,sys
from pathlib import Path
SESSION='f71d0252-ef1e-4b5c-b299-c2429ebcfbdf'
WORK='/var/tmp/tilexr-combine-ht-'+SESSION+'-v11'
def main(args):
    key=Path(os.environ['USERPROFILE'])/'.ssh/id_ed25519'
    ssh=['ssh','-T','-i',str(key),'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','root@141.61.94.26']
    def call(argv,payload=None,timeout=60):
        p=subprocess.run(ssh+[shlex.join(map(str,argv))],input=payload,capture_output=True,timeout=timeout)
        assert p.returncode==0, 'diagnostic_transport_exit_'+str(p.returncode)
        return p.stdout
    for name in ['probe_container.py','sst_fault.c','stdev_probe.cpp']:
        call(['bash','-c','umask 077; cat > "$1"','upload',WORK+'/'+name],Path(__file__).with_name(name).read_bytes())
    result=call(['docker','exec','tilexr-combine-ht-f71d0252-v11','python3','/work/probe_container.py',json.dumps(args)],timeout=args.get('timeout',360)+90)
    value=json.loads(result)
    print(json.dumps(value))
    return 0 if value['ok'] else 1
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--args-file',type=Path,required=True)
    sys.exit(main(json.loads(p.parse_args().args_file.read_text(encoding='utf-8'))))
