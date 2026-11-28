"""Collect dependency metadata without exporting source files."""
import configparser
import json
import pathlib
import re

from common import REPO, report, run, ssh_script

commit = run(['git','-C',str(REPO),'rev-parse','HEAD'], 'prerequisites-head')
paths = run(['git','-C',str(REPO),'ls-files'], 'prerequisites-files').splitlines()
dependency_paths = [p for p in paths if re.search(r'(^|/)(requirements[^/]*|[^/]*requirements[^/]*\.txt|pyproject\.toml|setup\.cfg|\.gitmodules)$',p,re.I)]
requirements = {}
for name in dependency_paths[:25]:
    path = REPO / name
    if path.is_file() and path.suffix == '.txt':
        requirements[name] = [s.strip() for s in path.read_text(encoding='utf-8',errors='replace').splitlines()
                              if s.strip() and not s.lstrip().startswith('#')][:90]
submodules = []
modules = REPO / '.gitmodules'
if modules.is_file():
    parser = configparser.ConfigParser()
    parser.read(modules, encoding='utf-8')
    submodules = [dict(parser[section]) for section in parser.sections()]
configs = []
config_paths = [p for p in paths if p.startswith('.codehub/config/llm/moe_91B_A5B_dsa')]
try:
    import yaml
except ImportError:
    yaml = None
for name in config_paths[:20]:
    path = REPO / name
    if not path.is_file():
        continue
    content = path.read_text(encoding='utf-8',errors='replace')
    selected = {}
    if yaml:
        def visit(value, prefix=''):
            if isinstance(value,dict):
                for key,item in value.items():
                    full = prefix + '.' + str(key) if prefix else str(key)
                    if isinstance(item,dict):
                        visit(item,full)
                    elif re.search(r'path|token|layer|hidden|head|batch|train|step|iter|data|mock|vocab|parallel|seq|fp8|precision|load|save|warm|checkpoint|lr|moe',str(key),re.I):
                        selected[full] = item
        try:
            visit(yaml.safe_load(content))
        except Exception:
            selected['yaml_parse'] = 'unavailable'
    configs.append({'path':name,'selected_settings':dict(list(selected.items())[:100])})
file_metadata = {'dependency_files':dependency_paths,'submodules':submodules,'config_paths':config_paths,
                 'requirements':requirements,'configs':configs,
                 'top_level_entries':sorted(p.name for p in REPO.iterdir() if p.name != '.git')}
deployed = '/home/ma-user/work/pangu_sophon_pytorch/ma-0/' + commit
remote = ssh_script('ma-0','remote_prerequisites.py',deployed,timeout=180)
report('prerequisites-detail', {'repository_metadata':file_metadata,'ma-0':remote})
