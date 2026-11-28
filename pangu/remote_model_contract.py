"""Inspect model/test controls and verify imports without training or downloads."""
import ast
import json
import os
import pathlib
import re
import subprocess
import sys

import yaml

repo = pathlib.Path(sys.argv[1])
important = re.compile(r'layer|hidden|head|batch|train|step|iter|data|mock|vocab|parallel|seq|fp8|precision|token|load|save|warm|checkpoint|lr|moe|expert', re.I)
configs = []
for path in sorted((repo / '.codehub/config/llm/moe_91B_A5B_dsa_cut').glob('*.yaml')):
    obj = yaml.safe_load(path.read_text())
    selected = {}
    def visit(value, prefix=''):
        if isinstance(value,dict):
            for key,item in value.items():
                full = prefix + '.' + str(key) if prefix else str(key)
                if isinstance(item,(dict,list)):
                    visit(item,full)
                elif important.search(str(key)):
                    selected[full] = item
        elif isinstance(value,list):
            for i,item in enumerate(value):
                visit(item,prefix + '.' + str(i))
        elif isinstance(value,str):
            m = re.match(r'\s*--?([\w-]+)(?:\s+|=)?(.*)',value)
            if m and important.search(m.group(1)):
                selected[m.group(1)] = m.group(2)
    visit(obj)
    configs.append({'path':path.relative_to(repo).as_posix(),'yaml_type':type(obj).__name__,
                    'top_keys':list(obj)[:12] if isinstance(obj,dict) else [],'selected_settings':selected})

options = []
tokenizer_types = set()
locations = []
for path in (repo / 'PanGu').rglob('*.py'):
    text = path.read_text(encoding='utf-8',errors='replace')
    if not re.search(r'mock.data|NullTokenizer|tokenizer.type|vocab.size',text,re.I):
        continue
    if len(locations) < 25:
        locations.append(path.relative_to(repo).as_posix())
    try:
        tree = ast.parse(text)
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.fullmatch(r'\w*Tokenizer',node.value):
            tokenizer_types.add(node.value)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr == 'add_argument':
            flags = [a.value for a in node.args if isinstance(a,ast.Constant) and isinstance(a.value,str) and a.value.startswith('--')]
            if any(re.search(r'mock|tokenizer|vocab-size|train-iters|train-samples|exit-interval',f) for f in flags):
                details = {}
                for kw in node.keywords:
                    if kw.arg in ('default','action','help','choices','required'):
                        try: details[kw.arg] = ast.literal_eval(kw.value)
                        except (ValueError,TypeError): pass
                options.append({'file':path.relative_to(repo).as_posix(),'flags':flags,'details':details})

tokenizers = []
for name in ['tokenizer.json','tokenizer_config.json']:
    path = pathlib.Path.home() / 'work' / name
    if path.is_file():
        data = json.loads(path.read_text())
        model = data.get('model',{})
        tokenizers.append({'path':str(path),'model_type':model.get('type'),'vocabulary_count':len(model.get('vocab',{})),
                           'tokenizer_class':data.get('tokenizer_class'),'bos_token':data.get('bos_token'),
                           'eos_token':data.get('eos_token'),'added_token_count':len(data.get('added_tokens',[]))})

env = os.environ.copy()
env['PYTHONPATH'] = str(repo) + os.pathsep + env.get('PYTHONPATH','')
env.update({'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_DATASETS_OFFLINE':'1'})
probe = 'import json, torch, numpy; import torch_npu; import PanGu; print(json.dumps({"torch":torch.__version__,"torch_npu":torch_npu.__version__,"numpy":numpy.__version__,"pangu_path":PanGu.__file__}))'
p = subprocess.run([sys.executable,'-c',probe],cwd=repo,env=env,capture_output=True,text=True,timeout=90)
print(json.dumps({'configs':configs,'dataset_and_tokenizer_controls':options[:50],
                 'tokenizer_types':sorted(tokenizer_types),'relevant_code_locations':locations,'local_tokenizer_metadata':tokenizers,
                 'pangu_import':{'exit_code':p.returncode,'stdout_tail':p.stdout.splitlines()[-12:],
                                 'stderr_tail':p.stderr.splitlines()[-45:]}}))
