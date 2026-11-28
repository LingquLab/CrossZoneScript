import ast
import json
import pathlib
import sys

repo = pathlib.Path(sys.argv[1])
names = ['tests/group1/unit_test/datasets/omni_dataset/test_offline_tail_pack.py',
         'PanGu/data_pipeline/omni/dataset.py','PanGu/data_pipeline/omni/utils.py']
results = []
expressions = []
for name in names:
    tree = ast.parse((repo/name).read_text())
    classes = []
    for node in ast.walk(tree):
        if isinstance(node,ast.ClassDef) and ('Synthetic' in node.name or 'Offline' in node.name):
            classes.append({'name':node.name,'bases':[ast.unparse(b) for b in node.bases],
                            'methods':[{'name':m.name,'arguments':ast.unparse(m.args)} for m in node.body if isinstance(m,ast.FunctionDef)]})
        if isinstance(node,ast.Call):
            call = ast.unparse(node.func)
            if any(s in call for s in ('SyntheticOffline','prepare','finalize','add_item','pickle.dumps','IndexedDatasetBuilder','end_document')):
                if len(expressions) < 8 and (name.endswith('test_offline_tail_pack.py') or 'add_item' in call or 'pickle.dumps' in call):
                    expressions.append({'file':name,'expression':ast.unparse(node)[:1600]})
    results.append({'file':name,'classes':classes})
print(json.dumps({'interfaces':results,'usage_expressions':expressions}))
