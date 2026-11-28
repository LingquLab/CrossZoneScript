"""Read the most recent smoke result and first worker tracebacks without rerunning it."""
import json
import pathlib
import re

root = pathlib.Path.home() / 'work/pangu_validation/ma-0/8da52234-c88f-451d-95a1-fa33a978690b'
runs = sorted(root.glob('gqa-smoke-*'))
assert runs
latest = runs[-1]
lines = (latest / 'supervisor.log').read_text(errors='replace').splitlines()
blocks = []
for i,line in enumerate(lines):
    if re.search(r'\[rank[01]\]: Traceback',line):
        block = []
        rank = re.search(r'\[rank[01]\]',line).group(0)
        for following in lines[i:i+90]:
            if following.startswith(rank):
                block.append(following)
                if re.search(r'\]: \w*(?:Error|Exception):',following):
                    break
        if block and not any(b == block for b in blocks):
            blocks.append(block)
        if len(blocks) >= 2:
            break
print(json.dumps({'read_only':True,'run_directory':str(latest),'worker_tracebacks':blocks,
                 'early_errors':[line[:1000] for line in lines if re.search(r'Error:|SMOKE_GUARD|ModuleNotFound|AssertionError',line)][:12]}))
