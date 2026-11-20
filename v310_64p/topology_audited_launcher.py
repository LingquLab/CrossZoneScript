import ast
import hashlib
import json
from pathlib import Path
import sys


def observed_load(model, directory):
    files = {}

    def on_open(event, args):
        if event != 'open' or not isinstance(args[0], str):
            return
        path = Path(args[0])
        if path.suffix in ('.yaml', '.yml') and path.name not in files:
            files[path.name] = str(path.resolve())

    hook_active = True

    def hook(event, args):
        if hook_active:
            on_open(event, args)

    sys.addaudithook(hook)
    try:
        return model.load_config(directory)
    finally:
        hook_active = False
        config = getattr(model, 'data', {})
        architecture = config.get('architecture_config', {})
        stars = config.get('simulator_config', {}).get('stars', {}).get('params', {})
        v310_fields = ('chip_num', 'aicore_num', 'aicore_num_per_die', 'subcore_num',
                       'host_num', 'enable_dump')
        selected = {name: value for name, value in architecture.items()
                    if name in v310_fields and isinstance(value, (str, int, bool))}
        evidence = {'directory': str(Path(directory).resolve()),
                    'files': {name: [path, hashlib.sha256(Path(path).read_bytes()).hexdigest()]
                              for name, path in files.items() if Path(path).is_file()},
                    'architecture': selected,
                    'stars': {name: stars[name] for name in v310_fields if name in stars}}
        with Path('config-load.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(evidence, separators=(',', ':'))+'\n')


class ObserveConfigurationCalls(ast.NodeTransformer):
    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'load_config':
            updated = ast.Call(func=ast.Name(id='observed_load', ctx=ast.Load()),
                               args=[node.func.value, *node.args], keywords=node.keywords)
            return ast.copy_location(updated, node)
        return node


launcher = Path(__file__).with_name('test_UBusSim.py')
tree = ast.parse(launcher.read_bytes(), filename=str(launcher))
ast.fix_missing_locations(ObserveConfigurationCalls().visit(tree))
exec(compile(tree, str(launcher), 'exec'),
     {'__file__': str(launcher), '__name__': '__main__', 'observed_load': observed_load})
