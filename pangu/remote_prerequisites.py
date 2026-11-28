"""Bounded read-only asset discovery and CLI import check on one idle candidate."""
import importlib.metadata
import json
import os
import pathlib
import re
import subprocess
import sys

repo = pathlib.Path(sys.argv[1])
assert (repo / '.cross-zone-deployment.json').is_file()
work = pathlib.Path.home() / 'work'
relevant = re.compile(r'pangu|sophon|token|data|2026develop1110|train|model|checkpoint|require|install|\.whl$', re.I)
top = sorted(p for p in work.iterdir() if relevant.search(p.name) and not p.name.startswith('.'))
assets = []
visited = 0
for root in top:
    if not root.is_dir() or root.is_symlink():
        continue
    for current, dirs, files in os.walk(root):
        visited += 1
        depth = len(pathlib.Path(current).relative_to(root).parts)
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in ('node_modules','__pycache__','site-packages')) if depth < 4 else []
        hits = [name for name in files if name in ('tokenizer.json','tokenizer.model','tokenization_pangu.py','vocab.json','vocab.txt','merges.txt')
                or name.endswith(('.idx','.whl'))]
        if hits:
            assets.append({'directory':current,'files':hits[:12]})
        if visited >= 3500 or len(assets) >= 60:
            break
    if visited >= 3500 or len(assets) >= 60:
        break
env = os.environ.copy()
env['PYTHONPATH'] = str(repo) + os.pathsep + env.get('PYTHONPATH','')
env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1'})
logs = work / 'pangu_validation' / 'ma-0' / '8da52234-c88f-451d-95a1-fa33a978690b'
logs.mkdir(parents=True, exist_ok=True)
try:
    p = subprocess.run([sys.executable,'launch.py','--help'], cwd=repo, env=env, capture_output=True, text=True, timeout=90)
    (logs / 'launch-help.stdout').write_text(p.stdout)
    (logs / 'launch-help.stderr').write_text(p.stderr)
    cli = {'exit_code':p.returncode,'options':sorted(set(re.findall(r'--[A-Za-z][\w-]+',p.stdout)))[:100],
           'error_tail':p.stderr.splitlines()[-40:] if p.returncode else [],'stdout_tail':p.stdout.splitlines()[-8:] if p.returncode else []}
except subprocess.TimeoutExpired:
    cli = {'status':'TIMED_OUT','timeout_seconds':90}
versions = {d.metadata['Name']:d.version for d in importlib.metadata.distributions()
            if re.search(r'pangu|megatron|mind|hyper|torch|flash|fusion|triton|cann|apex|hydra|numpy|transform', d.metadata.get('Name',''), re.I)}
print(json.dumps({'relevant_work_entries':[{'name':p.name,'directory':p.is_dir()} for p in top],
                 'asset_candidates':assets,'asset_search_directories':visited,'packages':versions,
                 'launch_help':cli,'logs':str(logs)}))
