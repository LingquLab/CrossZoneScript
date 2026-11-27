"""Report simulator runtime files, model settings and host capacity."""
import ast
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

PLATFORM_PROBE = r"""
import ast
import hashlib
import json
from pathlib import Path
import re
import yaml

root = Path('/opt/sim')
sim = root / 'simulator'
result = {}
for group, names in {
    'v310_deployment': ('daw_config.yml', 'davidV100_simulator_config.yml', 'ddie_noc_config.yml', 'udie_noc_config.yml'),
    'UBusSim_deployment': ('test_UBusSim.py', 'UBusSim_simulator_config.yml', 'UBusSim_simulator_config_topo.yml'),
}.items():
    for name in names:
        paths = list((sim/group).rglob(name))
        result[name] = [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths[:5]]
        if name == 'davidV100_simulator_config.yml' and len(paths) == 1:
            raw = paths[0].read_text()
            config = yaml.safe_load(raw)['simulator_config']
            result['scalar'] = config['su'].get('component')
            result['lsu_level'] = config['su']['params'].get('lsu_level')
            result['core_frequency'] = re.findall(r'freq_aicore:\s*([.\w]+)',raw)
        if name == 'test_UBusSim.py' and len(paths) == 1:
            tree = ast.parse(paths[0].read_text())
            guard = ast.dump(ast.parse('if __name__ == "__main__": pass').body[0].test)
            result['model_main_guard'] = isinstance(tree.body[-1], ast.If) and ast.dump(tree.body[-1].test) == guard
result['plugins'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (root/'plugins').glob('lib*Simulator*.so') if p.is_file()}
print(json.dumps(result))
"""


def host_probe(args):
    import os

    def docker(*argv):
        return subprocess.check_output(['docker', *argv], stderr=subprocess.PIPE, timeout=45)

    inspected = json.loads(docker('inspect', args['container']))[0]
    assert inspected['Image'] == args['image_id'] and inspected['State']['Running']
    available = int(re.search(r'^MemAvailable:\s+(\d+)', Path('/proc/meminfo').read_text(), re.M)[1]) * 1024
    workspace = Path(args['workspace'])
    root = workspace / 'tilexr-sim-jobs' / 'be22947c-5e6b-4fb8-a268-af2f61eba704'
    state = root / 'run-49f846b-a1'
    progress = state / 'progress.json'
    record = {'memory_available_bytes': available, 'affinity_cpus': len(os.sched_getaffinity(0)),
              'workspace_free_bytes': shutil.disk_usage(workspace).free,
              'old_template_present': Path('/mnt/share/tilexr-sim-jobs/b4f4660a-6144-453c-99f7-339b40f56c54/inter-server-native/t4-sst-mte-bulk-forward-hotpath-1a2f3549/case-manifest.json').is_file(),
              'bootstrap_progress': json.loads(progress.read_text()) if progress.is_file() else None}
    p = json.loads(docker('exec', inspected['Id'], 'python3', '-c', PLATFORM_PROBE))
    print(json.dumps({**record, 'platform': p}))
    return 0


def main(args=None):
    if args is not None:
        return host_probe(args)

    import argparse
    from ssh_target import run

    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', required=True, type=Path)
    request = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    return 0 if run(Path(__file__).read_text().rsplit('if __name__', 1)[0], request, 'platform-probe', 90) else 1


if __name__ == '__main__':
    raise SystemExit(main())
