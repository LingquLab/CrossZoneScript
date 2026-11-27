"""Run or inspect one matrix point in the private Combine-HT simulator container."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

SESSION = 'be22947c-5e6b-4fb8-a268-af2f61eba704'
TOOLS = ('fixture.py', 'prefill.cpp', 'sim_case.py', 'return_case.py', 'addressed_exports.py',
         'profile_case.py', 'clock_evidence.json', 'joint_evidence.py', 'inter_snapshot.py',
         'platform_stage.py')
CHOICES = ('local-a', 'local-b', 'inter-a', 'inter-b', 'joint-a', 'joint-b')


def read(path):
    return json.loads(path.read_text())


def configs(bs, point):
    variant = point[-1]
    kind = point.rsplit('-', 1)[0]
    stage = {'local': 'local-send', 'inter': 'inter-server-send'}.get(kind)
    if kind == 'joint':
        dedicated = (variant == 'a') != (bs == 32)
        stage = 'joint-final' if dedicated else 'joint-reuse-final'
    intra, peer_slots = (4, 1) if variant == 'a' else (8, 2)
    return stage, intra, peer_slots


def host_main(args):
    import fcntl

    root = Path(args['workspace']) / 'tilexr-sim-jobs' / SESSION / 'run-49f846b-a1'
    identity = read(root/'identity.json')
    cid = identity['container_id']
    docker = ['docker', 'exec', cid]
    info = json.loads(subprocess.check_output(['docker','inspect',cid], timeout=15))[0]
    assert info['Image'] == identity['image'] == args['image_id']
    assert info['Config']['Labels']['crosszone.session'] == SESSION
    assert info['State']['Running'] and info['HostConfig']['NetworkMode'] == 'none'
    assert read(root/'bootstrap-result.json')['profile']['all_compiled']
    bs, point, retry = args['bs'], args['point'], args.get('retry', 0)
    assert bs in (4, 32, 128) and point in CHOICES
    stage, intra, slots = configs(bs, point)
    attempt = 1 + (point.endswith('b')) + 2 * retry
    case = root/'cases'/f't{bs}-{stage}-a{attempt}'
    receipt = case/'phase-result.json'
    runner = root/f'runner-{bs}-{point}-{retry}.log'
    platform_log = root/'platform-stage.log'

    def summary():
        state = root/'progress.json'
        result = {'case': str(case), 'point': point, 'retry': retry}
        for key, path in [('result', receipt), ('run', case/'run.json'), ('progress', state)]:
            if path.is_file():
                saved = read(path)
                saved.pop('command', None)
                if key == 'run':
                    saved['sst_elapsed_seconds'] = round(time.time()-saved['started_unix'], 2)
                result[key] = saved
        log = case/'sim.log'
        if log.is_file():
            with log.open('rb') as file:
                size = file.seek(0, 2)
                file.seek(max(0,size-65536))
                tail = file.read().decode(errors='replace')
            cycles = re.findall(r'Sim: (\d+)\(cycle\)', tail)
            result.update(sim_log_bytes=size, last_cycle=int(cycles[-1]) if cycles else None)
            result['sim_diagnostics'] = [line[:260] for line in tail.splitlines()
                                         if re.search(r'fatal|memory|exception|error|simulated time:',line,re.I)][-6:]
        if not receipt.is_file() and args.get('detail'):
            for path in (platform_log, runner):
                if path.is_file():
                    result[path.name] = path.read_text(errors='replace').splitlines()[-8:]
        print(json.dumps(result, separators=(',', ':')))
        return 0

    if args.get('action') == 'status':
        return summary()

    timeout = args['timeout']
    assert 0 < timeout <= 7200 and not case.exists()
    if bs != 4:
        for earlier in (n for n in (4,32) if n < bs):
            for name in CHOICES:
                before, _, _ = configs(earlier, name)
                cases = sorted((root/'cases').glob(f't{earlier}-{before}-a*/phase-result.json'))
                assert any((passed := read(path))['status'] == 'sst_and_checker_passed'
                           and passed['intra_cores'] == configs(earlier,name)[1]
                           and passed['peer_slots'] == configs(earlier,name)[2]
                           and passed['candidate_commit'] == identity['revision'] for path in cases), (earlier,name)
    with (root/'matrix.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        files = args['tools']
        assert set(files) == set(TOOLS)
        tag = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16]
        work = root/'tools'/tag
        work.mkdir(parents=True, exist_ok=True)
        for name, contents in files.items():
            path = work/name
            if not path.exists():
                path.write_bytes(contents.encode())
            assert path.read_bytes() == contents.encode()
        inside = Path('/work/tools')/tag
        manifest = root/'platform-manifest.json'
        if not manifest.is_file():
            temp = root/'platform-pending'
            assert not temp.exists()
            with platform_log.open('xb') as log:
                checked = subprocess.run(docker+['python3',str(inside/'platform_stage.py'),'/work/platform-pending'],
                                         stdout=log,stderr=subprocess.STDOUT,timeout=120)
            if checked.returncode:
                args['detail'] = True
                return summary()
            temp.rename(root/'platform')
            (root/'platform'/'.manifest.json').rename(manifest)
        config = dict(revision=identity['revision'], action=stage, tokens=bs, attempt=attempt,
                      timeout=timeout, profiling=True, intra_cores=intra, peer_slots=slots,
                      platform_sha256=read(manifest), sst_threads=8)
        overhead = {4:300,32:540,128:950}[bs]
        with runner.open('xb') as log:
            subprocess.run(docker+['timeout','--kill-after=25',str(timeout+overhead),
                                   'python3',str(inside/'sim_case.py'),json.dumps(config)],
                           stdout=log,stderr=subprocess.STDOUT,timeout=timeout+overhead+40)
    return summary()


def campaign(args):
    from contextlib import redirect_stdout
    import fcntl
    import io

    bs = args['bs']
    assert bs in (4, 32, 128)
    revision = '49f846bdba882ecc0b708b0ec18f56d474b252c0'
    root = Path(args['workspace']) / 'tilexr-sim-jobs' / SESSION / 'run-49f846b-a1'
    journal = root / f'campaign-bs{bs}.json'
    rows = []
    with (root/'campaign.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        for point in CHOICES:
            stage, intra, slots = configs(bs, point)
            receipts = (root/'cases').glob(f't{bs}-{stage}-a*/phase-result.json')
            saved = [
                path for path in receipts
                if (record := read(path))['status'] == 'sst_and_checker_passed'
                and record['intra_cores'] == intra and record['peer_slots'] == slots
                and record['candidate_commit'] == revision
            ]
            if saved:
                row = {'point': point, 'case': str(saved[0].parent), 'result': read(saved[0])}
            else:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    host_main(dict(args, action='run', point=point, detail=True))
                row = json.loads(buf.getvalue())
            for key in ('result', 'run'):
                if key in row:
                    row[key].pop('command', None)
            rows.append(row)
            temp = journal.with_suffix('.tmp')
            temp.write_text(json.dumps(rows, indent=2))
            temp.replace(journal)
            if row.get('result', {}).get('status') != 'sst_and_checker_passed':
                break
    print(json.dumps({'bs': bs, 'passed': sum(row.get('result', {}).get('status') == 'sst_and_checker_passed'
                                              for row in rows), 'rows': rows}, separators=(',', ':')))
    return 0



def main(args=None):
    if args is not None:
        return campaign(args) if args.get('action') == 'campaign' else host_main(args)

    import argparse
    from ssh_target import run

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    request = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    action = request.get('action', 'run')
    if action in ('run', 'campaign'):
        request['tools'] = {name: Path(__file__).with_name(name).read_text(encoding='utf-8') for name in TOOLS}
    seconds = request.get('timeout', 0) + ({4:550,32:790,128:1200}[request['bs']] if action=='run' else 60)
    if action == 'campaign':
        seconds = seconds + 6*(request['timeout'] + 1300)
    code = Path(__file__).read_text(encoding='utf-8').rsplit('if __name__', 1)[0]
    return 0 if run(code, request, f"{action}-{request['bs']}-{request.get('point', 'all')}", seconds) else 1


if __name__ == '__main__':
    raise SystemExit(main())
