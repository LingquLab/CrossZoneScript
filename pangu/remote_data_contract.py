"""Read dataset interfaces and fixture metadata, without reading training records."""
import ast
import json
import pathlib
import re
import sys

repo = pathlib.Path(sys.argv[1])
paths = [p for root in ['tools','tests','PanGu/data_pipeline'] for p in (repo/root).rglob('*.py')
         if re.search(r'mock|fake|dummy|preprocess|offline|dataset|binary|serialize',p.name,re.I)]
metadata = []
for path in paths[:100]:
    text = path.read_text(encoding='utf-8',errors='replace')
    try: tree = ast.parse(text)
    except SyntaxError: continue
    definitions = []
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.ClassDef)):
            definitions.append({'name':n.name,'doc':(ast.get_docstring(n) or '')[:260]})
    metadata.append({'file':path.relative_to(repo).as_posix(),'definitions':definitions[:12],
                     'supports_mock_data':bool(re.search(r'args\.mock_data|[\"\']mock_data[\"\']',text)),
                     'serialization_tools':sorted(set(re.findall(r'\b(torch\.save|torch\.load|pickle\.dump|pickle\.load|np\.save|np\.load|IndexedDatasetBuilder|BinWriter|OfflineDataset)\b',text)))})

provider = repo / 'PanGu/data_pipeline/omni_dataset_provider.py'
tree = ast.parse(provider.read_text())
provider_details = []
for n in ast.walk(tree):
    if isinstance(n,ast.Call):
        name = ast.unparse(n.func)
        if re.search(r'Dataset|dataset|bin_files|config',name):
            keywords = {kw.arg:ast.unparse(kw.value) for kw in n.keywords if kw.arg}
            provider_details.append({'call':name,'keywords':keywords,'positional_argument_count':len(n.args)})

asset = pathlib.Path.home() / 'work/MultiModal_512K_Offline_Data'
asset_metadata = []
if asset.is_dir():
    for path in sorted(asset.iterdir())[:20]:
        item = {'name':path.name,'directory':path.is_dir(),'bytes':path.stat().st_size if path.is_file() else None}
        if path.is_dir():
            item['children'] = [{'name':p.name,'directory':p.is_dir(),'bytes':p.stat().st_size if p.is_file() else None} for p in sorted(path.iterdir())[:8]]
        asset_metadata.append(item)

print(json.dumps({'dataset_related_files':metadata,'provider_calls':provider_details[:30],
                 'existing_offline_asset_directory':str(asset),'existing_offline_asset_metadata':asset_metadata}))
