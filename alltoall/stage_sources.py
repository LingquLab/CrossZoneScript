"""Use TileXR's fixed staging tool and isolated source/tool checkouts."""
import json
import os
from pathlib import Path
import subprocess
import sys

SESSION='b4f4660a-6144-453c-99f7-339b40f56c54'
REPORT=Path('D:/project/tilexr-sim-reports')/SESSION
REPO=Path('D:/project/tileXR')
RUNTIME='85afd1fa61669a5ef800a80bec4df93745f119c6'
TOOL='96581a23de5e7e87ce90c770fa130a596600944e'
GENERATOR='23a542b2e6bfcc34f85313d02677a4aafdfc3efb'


def run(argv, label, timeout=120):
    env=os.environ.copy()
    env.update(PYTHONIOENCODING='utf-8',PYTHONUTF8='1')
    with (REPORT/(label+'.log')).open('wb') as log:
        p=subprocess.run([str(x) for x in argv],stdout=log,stderr=subprocess.STDOUT,env=env,timeout=timeout)
    if p.returncode:
        raise RuntimeError(label+' failed; inspect green-local '+str(REPORT/(label+'.log')))


container=json.loads((REPORT/'container.json').read_text(encoding='utf-8'))
for name,rev in (('runtime',RUNTIME),('tool',TOOL)):
    p=subprocess.run(['git','-C',str(REPO),'cat-file','-e',rev+'^{commit}'],capture_output=True,timeout=10)
    if p.returncode:
        run(['git','-C',REPO,'fetch','origin',rev],'fetch-'+name,180)
    target=Path('D:/project/tileXR-worktrees')/('sst-b4f4660a-'+name)
    if target.exists():
        raise RuntimeError('Isolated worktree already exists; inspect before continuing')
    run(['git','-C',REPO,'worktree','add','--detach',target,rev],'worktree-'+name)
runtime_tree=Path('D:/project/tileXR-worktrees/sst-b4f4660a-runtime')
tool_tree=Path('D:/project/tileXR-worktrees/sst-b4f4660a-tool')
inventory=REPORT/'inventory.json'
inventory.write_text(json.dumps({'version':1,'clusters':{'cpu-sim':{'user':'root','hosts':[{'name':'host-94-58','address':'141.61.94.58'}]}}}),encoding='utf-8')
request=REPORT/'target.json'
target={k:container[k] for k in ('container','container_id','image_id')}
target.update(cluster='cpu-sim',host='host-94-58',hostname='host-94-58')
request.write_text(json.dumps(target),encoding='utf-8')
run([sys.executable,tool_tree/'tools/simulator/sources.py','--repository',runtime_tree,
     '--revision',RUNTIME,'--tooling-revision',GENERATOR,'--request',request,
     '--inventory',inventory,'--stage-root','/work/lr8-source','--local-output',REPORT/'source'],
    'stage-sources',300)
result=json.loads((REPORT/'source/result.json').read_text(encoding='utf-8'))
assert result['state']=='candidate_sources_staged_not_built'
print(json.dumps({'state':result['state'],'sources':result['sources'],
                  'source_lock_sha256':result['source_lock_sha256'],
                  'runtime_commit':result['runtime_commit'],'tool_commit':TOOL,
                  'tooling_commit':GENERATOR,'report':str(REPORT)},ensure_ascii=True))
