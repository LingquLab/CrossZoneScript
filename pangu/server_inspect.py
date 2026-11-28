"""Inspect only deployment-relevant installed modules and asset locations."""
import importlib.util
import json
import os
import pathlib
import subprocess

home = pathlib.Path.home()
r = {'pythonpath_present':bool(os.environ.get('PYTHONPATH'))}
r['module_available'] = {name:importlib.util.find_spec(name) is not None for name in
                         ['torch','torch_npu','transformers','megatron','mindspeed','hyper_parallel','datasets','yaml','einops','sentencepiece','tensorboard','omegaconf','hydra','apex','moxing']}
r['locations'] = {}
for root in [home / 'work', pathlib.Path('/cache'), pathlib.Path('/data'), pathlib.Path('/mnt'), pathlib.Path('/home/ma-user/modelarts/inputs')]:
    if root.is_dir():
        r['locations'][str(root)] = sorted(p.name for p in root.iterdir() if not p.name.startswith('.'))[:35]
r['toolkit_links'] = {str(p):str(p.resolve()) for p in
                      [pathlib.Path('/usr/local/Ascend/cann'), pathlib.Path('/usr/local/Ascend/ascend-toolkit/latest')] if p.exists()}
print(json.dumps(r))
