"""Deploy a commit snapshot through the PC and summarize example prerequisites."""
import ast
import concurrent.futures
import json
import pathlib
import re
import shutil

from common import ALIASES, LOGS, REPO, report, run, sha256, ssh_script

commit = run(['git','-C',str(REPO),'rev-parse','HEAD'], 'source-head')
assert run(['git','-C',str(REPO),'status','--porcelain'], 'source-status') == ''
artifact_dir = pathlib.Path('D:/cross_zone/artifacts')
artifact_dir.mkdir(exist_ok=True)
archive = artifact_dir / ('pangu-' + commit + '.tar')
run(['git','-C',str(REPO),'archive','--format=tar','-o',str(archive),commit], 'source-archive', timeout=180)
digest = sha256(archive)

examples = []
referenced = set()
for path in sorted((REPO / 'scripts' / 'example' / 'llm').glob('*.sh')):
    text = path.read_text(encoding='utf-8')
    variables = {m.group(1):m.group(2).strip().strip('\"\'') for m in
                 re.finditer(r'(?m)^\s*(?:export\s+)?(BASE_DATA_PATH|GPUS_PER_NODE|ASCEND_RT_VISIBLE_DEVICES|VC_WORKER_HOSTS)=(.*)$', text)}
    flattened = text.replace('\\\n', ' ')
    args = {m.group(1):m.group(2).strip() for m in re.finditer(r'--([\w-]+)([^\n]*?)(?=\s--|\n|$)', flattened)}
    for value in args.values():
        for candidate in re.findall(r'[\w./-]+\.ya?ml', value):
            referenced.add(candidate)
    examples.append({'script':path.relative_to(REPO).as_posix(),'environment':variables,'arguments':args})

configs = []
for value in sorted(referenced):
    path = REPO / value
    if path.is_file():
        text = path.read_text(encoding='utf-8', errors='replace')
        selected = {}
        for line in text.splitlines():
            m = re.match(r'\s*([\w-]+)\s*:\s*(.*?)\s*(?:#.*)?$', line)
            if m and re.search(r'path|token|layer|hidden|head|batch|train|step|iter|data|mock|vocab|parallel|sequence|seq_|fp8|precision|load|save|warm|checkpoint', m.group(1), re.I):
                selected[m.group(1)] = m.group(2)[:240]
        configs.append({'path':value,'selected_settings':selected})

requirements = {}
for path in sorted(REPO.glob('*requirements*.txt')):
    requirements[path.name] = [line.strip() for line in path.read_text(encoding='utf-8',errors='replace').splitlines()
                              if line.strip() and not line.lstrip().startswith('#')][:100]

imports = {}
for name in ['launch.py','train.py','pretrain_gpt.py','setup.py','PanGu/__init__.py']:
    path = REPO / name
    if path.is_file():
        try:
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            imports[name] = sorted({n.module.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
                                   | {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names})
        except SyntaxError:
            imports[name] = ['AST inspection unavailable']

scp = shutil.which('scp')
assert scp
def deploy(alias):
    # The initial probe established ~/work exists on all three machines.
    remote_archive = '/home/ma-user/work/pangu-' + alias + '-' + commit + '.tar'
    run([scp,'-q','-o','BatchMode=yes','-o','ConnectTimeout=15',str(archive),alias + ':' + remote_archive], alias + '-upload', timeout=300)
    deployment = ssh_script(alias, 'remote_deploy.py', alias, commit, digest, remote_archive, timeout=180)
    environment = ssh_script(alias, 'server_inspect.py', timeout=90)
    return {'alias':alias,'deployment':deployment,'environment':environment}

with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    servers = list(pool.map(deploy, ALIASES))
report('deployment-and-prerequisites', {'commit':commit,'archive_bytes':archive.stat().st_size,'archive_sha256':digest,
       'llm_examples':examples,'referenced_configs':configs,'requirements':requirements,'entry_imports':imports,
       'servers':servers,'model_tests_run':False})
