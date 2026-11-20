import argparse
import json
from pathlib import Path
import subprocess
import sys

cli = argparse.ArgumentParser()
cli.add_argument('--args-file', required=True, type=Path)
args = json.loads(cli.parse_args().args_file.read_bytes())
here = Path(__file__).resolve().parent
operation = args['operation']
remote_command = 'docker exec -i simbox python3 -B -'
if operation == 'build':
    manifest = json.loads((here/'sha.json').read_bytes())
    resources = {'manifest': manifest, 'archive': (here/'sources.tar.gz').read_bytes(),
                 'compile_source': (here/'compile.sh').read_bytes()}
    seconds = 440
elif operation == 'stage':
    resources = {}
    seconds = 145
elif operation == 'run':
    resources = {}
    seconds = 4020
elif operation == 'report':
    resources = {}
    seconds = 225
elif operation == 'status':
    resources = {}
    seconds = 60
elif operation == 'debug':
    name = b'dispatch_tilelang_64p_top6_68d32608_run01'
    debug = b'dispatch_tilelang_64p_top6_68d32608_debug01'
    resources = {key + '_source': (here/f'{key}_remote.py').read_bytes().replace(name,debug)
                 for key in ('stage','run')}
    resources['run_source'] = resources['run_source'].replace(b'3600s',b'200s').replace(b'3640)',b'235)')
    remote_command = 'python3 -B -'
    seconds = 575
elif operation == 'compat':
    resources = {'stage_source': (here/'stage_remote.py').read_bytes().replace(
        b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_f1scalar01').replace(
        b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_f1scalar01')}
    seconds = 300
elif operation in ('run_compat','report_compat','status_compat'):
    resources = {}
    phase = operation.split('_')[0]
    seconds = {'run':4020,'report':225,'status':60}[phase]
elif operation == 'retry_output':
    def corrected(name):
        source = (here/f'{name}_remote.py').read_bytes()
        source = source.replace(b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_f1scalar01')
        return source.replace(b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_f1scalar02')
    stage = corrected('stage').replace(b'profile_size=36992',b'profile_size=37376').replace(b"'readback_bytes':56*32",b"'readback_bytes':2048")
    resources = {'stage':stage,'run':corrected('run')}
    seconds = 4180
elif operation == 'report_dies':
    resources = {'base_report': (here/'report_remote.py').read_bytes().replace(
        b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_f1scalar01').replace(
        b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_f1scalar01')}
    seconds = 225
elif operation in ('profile_build_stage','profile_run','profile_report','profile_status'):
    def profile_source(name):
        data=(here/f'{name}_remote.py').read_bytes()
        return data.replace(
            b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_profile03').replace(
            b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_profile03')
    if operation=='profile_build_stage':
        stage=profile_source('stage').replace(
            b'profile_offsets=(128,16512,24704,28800,32896)',
            b'profile_offsets=(512,66048,98816,131584,164352)').replace(
            b'profile_size=36992',b'profile_size=197120').replace(
            b'assert profile_offsets==(128,128+64*32*8,128+64*32*12,128+64*32*12+64*64,128+64*32*12+2*64*64)',
            b'assert profile_offsets==(512,512+64*1024,512+64*1536,512+64*2048,512+64*2560)').replace(
            b',64,32,32,16,0,*addresses',b',64,32,128,128,0,*addresses').replace(
            b"'readback_bytes':56*32",b"'readback_bytes':2048")
        resources = {'stage':stage}
    elif operation=='profile_report':
        base=profile_source('report').replace(b"('state_sender_gate',56*32)",b"('state_sender_gate',2048)")
        for old,new in ((b'cnt_o+64*core',b'cnt_o+512*core'),(b'drop_o+64*core',b'drop_o+512*core'),
                        (b'done_o+64*core',b'done_o+512*core'),(b'ticks_o+256*core',b'ticks_o+1024*core'),
                        (b'ids_o+128*core',b'ids_o+512*core')):
            base=base.replace(old,new)
        resources = {'base_report':base}
    else:
        resources = {}
    seconds = {'profile_build_stage':320,'profile_run':4020,'profile_report':225,'profile_status':60}[operation]
elif operation == 'report_aligned':
    resources = {}
    seconds = 225
else:
    raise ValueError('unknown operation')
remote = ''.join(f'{name} = {value!r}\n' for name,value in resources.items()).encode()
if operation.endswith('_compat'):
    remote += (here/f'{phase}_remote.py').read_bytes().replace(
        b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_f1scalar01').replace(
        b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_f1scalar01')
elif operation == 'retry_output':
    remote += b"import contextlib,io,json\nwith contextlib.redirect_stdout(io.StringIO()) as captured: exec(compile(stage,'stage','exec'),{})\nresult=json.loads(captured.getvalue())\nif result['status']=='PASS':exec(compile(run,'run','exec'),{})\nelse:print(json.dumps(result))\n"
elif operation == 'report_dies':
    remote += (here/'die_report_remote.py').read_bytes()
elif operation == 'profile_build_stage':
    remote += (here/'profile_build_remote.py').read_bytes()
    remote += b"\nresult=build()\nif result['status']=='PASS':exec(compile(stage,'stage','exec'),{})\nelse:print(json.dumps(result))\n"
elif operation == 'profile_run':
    remote += profile_source('run')
elif operation == 'profile_status':
    remote += profile_source('status')
elif operation == 'profile_report':
    remote += (here/'die_report_remote.py').read_bytes().replace(
        b'dispatch_tilelang_64p_top6_68d32608_f1scalar01',b'dispatch_tilelang_64p_top6_68d32608_profile03').replace(b'analysis-die-merged',b'analysis-02')
elif operation == 'report_aligned':
    remote += (here/'report_remote.py').read_bytes().replace(
        b'dispatch_tilelang_64p_68d32608_build01',b'dispatch_tilelang_64p_68d32608_f1scalar01').replace(
        b'dispatch_tilelang_64p_top6_68d32608_run01',b'dispatch_tilelang_64p_top6_68d32608_f1scalar02').replace(
        b"('state_sender_gate',56*32)",b"('state_sender_gate',2048)")
else:
    remote += (here/f'{operation}_remote.py').read_bytes()
compile(remote,operation,'exec')
argv=['ssh','-i',str(Path.home()/'.ssh/deploy_ed25519'),'-o','IdentitiesOnly=yes',
      '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=12',
      'root@141.61.94.18',remote_command]
p = subprocess.run(argv, input=remote, capture_output=True, timeout=seconds)
(Path.cwd()/f'tl64p-{operation}.stdout.log').write_bytes(p.stdout)
(Path.cwd()/f'tl64p-{operation}.stderr.log').write_bytes(p.stderr)
if p.returncode:
    result={'status':'REMOTE_FAILURE','operation':operation,'ssh_exit':p.returncode}
else:
    result=json.loads(p.stdout)
print(json.dumps(result,ensure_ascii=True,separators=(',',':')))
sys.exit(0 if result['status']=='PASS' or operation.startswith('report') or operation in ('profile_report','profile_build_stage','profile_run') else 1)
