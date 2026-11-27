"""Run the image's native AIV URMA smoke case in a private directory."""
import json
from pathlib import Path
import re
import subprocess
import time

SESSION = 'be22947c-5e6b-4fb8-a268-af2f61eba704'


def host_main(args):
    root = Path(args['workspace']) / 'tilexr-sim-jobs' / SESSION / 'run-49f846b-a1'
    record = json.loads((root/'identity.json').read_text())
    cid = record['container_id']
    info = json.loads(subprocess.check_output(['docker','inspect',cid], timeout=15))[0]
    assert info['Image'] == record['image'] == args['image_id']
    assert info['Config']['Labels']['crosszone.session'] == SESSION
    assert info['State']['Running'] and info['HostConfig']['NetworkMode'] == 'none'
    log = root / 'bundled-smoke-aiv-urma-installed-path.log'
    command = '''
        set -eo pipefail
        source /opt/sim/sst-env.sh
        export PYTHONPATH=/opt/sim/simulator:${PYTHONPATH:-}
        case=/opt/sim/simulator/Testcases/Smoke_aivUrma
        cd "$case"
        python3 -c 'import pathlib,yaml; d=yaml.safe_load(pathlib.Path("UBusSim_config/UBusSim_simulator_config.yml").read_text()); assert d["simulator_info"]["urma_flag"] and not d["simulator_info"]["simt_urma_flag"]'
        exec timeout --kill-after=10 360 /opt/sim/local/sstcore/libexec/sstsim.x -n 8 "$case/test_UBusSim.py"
    '''
    start = time.monotonic()
    with log.open('xb') as stream:
        proc = subprocess.run(['docker','exec',cid,'bash','-c',command],
                              stdout=stream,stderr=subprocess.STDOUT,timeout=395)
    with log.open('rb') as stream:
        stream.seek(max(0,stream.seek(0,2)-262144))
        tail = stream.read().decode(errors='replace')
    pattern = r'FATAL|simulated time:|[A-Za-z]+Error:|cannot|not found|errorcode'
    samples = [line[:280] for line in tail.splitlines() if re.search(pattern,line)][-8:]
    print(json.dumps({'case': '/opt/sim/simulator/Testcases/Smoke_aivUrma', 'exit': proc.returncode,
                      'wall_seconds': round(time.monotonic()-start,3), 'observations': samples}))
    return 0


def main(args=None):
    if args is not None:
        return host_main(args)

    import argparse
    from ssh_target import run

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    request = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    code = Path(__file__).read_text(encoding='utf-8').rsplit('if __name__', 1)[0]
    return 0 if run(code, request, 'smoke-aiv-urma', 430) else 1


if __name__ == '__main__':
    raise SystemExit(main())
