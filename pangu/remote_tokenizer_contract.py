import ast
import json
import pathlib
import sys

repo=pathlib.Path(sys.argv[1])
classes=[]
expressions=[]
for path in (repo/'PanGu').rglob('*.py'):
    if 'tokeniz' not in path.name.lower(): continue
    text=path.read_text(encoding='utf-8',errors='replace')
    try: tree=ast.parse(text)
    except SyntaxError: continue
    for node in ast.walk(tree):
        if isinstance(node,ast.ClassDef):
            attributes=[]
            for n in ast.walk(node):
                if isinstance(n,ast.Assign):
                    for target in n.targets:
                        if isinstance(target,ast.Attribute) and any(term in target.attr for term in ['unique_identifiers','tokenizer','eod','pad']):
                            attributes.append({'attribute':target.attr,'value_type':type(n.value).__name__,
                                               'factory':ast.unparse(n.value.func) if isinstance(n.value,ast.Call) else None,
                                               'factory_keyword_names':[kw.arg for kw in n.value.keywords] if isinstance(n.value,ast.Call) else []})
            classes.append({'file':path.relative_to(repo).as_posix(),'class':node.name,
                            'methods':[{'name':m.name,'arguments':ast.unparse(m.args)} for m in node.body if isinstance(m,ast.FunctionDef)][:20],
                            'attributes':attributes[:15]})
        if isinstance(node,ast.Call) and 'PretrainedFromHF' in ast.unparse(node.func) and len(expressions)<4:
            expressions.append({'file':path.relative_to(repo).as_posix(),'expression':ast.unparse(node)[:1500]})
    for line in text.splitlines():
        if 'unique_identifiers' in line and len(expressions)<8:
            expressions.append({'file':path.relative_to(repo).as_posix(),'expression':line.strip()[:800]})
work=pathlib.Path.home()/'work'
config=json.loads((work/'tokenizer_config.json').read_text())
print(json.dumps({'tokenizer_classes':classes[:28],'relevant_expressions':expressions,
                 'local_files':[p.name for p in work.iterdir() if p.is_file() and any(t in p.name for t in ['tokeniz','vocab','merges','special_tokens'])],
                 'local_config_metadata':{k:config.get(k) for k in ['tokenizer_class','auto_map','bos_token','eos_token','pad_token','unk_token','extra_special_tokens']}}))
