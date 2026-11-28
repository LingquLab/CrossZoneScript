"""Extract small fixture contracts, then verify the scoped c10d compatibility fix."""
import ast
import datetime
import json
import os
import pathlib
import signal
import subprocess
import sys

repo, shim_source = pathlib.Path(sys.argv[1]), sys.argv[2]
contracts = []
snippets = []
names = ['tests/group1/unit_test/datasets/omni_dataset/test_offline_tail_pack.py',
         'tests/group1/unit_test/datasets/omni_dataset/test_offline_prepare_finalize.py',
         'tests/group1/unit_test/datasets/omni_dataset/test_offline_dataset_v2.py',
         'PanGu/data_pipeline/core/indexed_dataset.py']
for name in names:
    path = repo / name
    tree = ast.parse(path.read_text())
    functions = []
    for node in ast.walk(tree):
        if isinstance(node,ast.FunctionDef) and (node.name in ('make_sample','__init__','add_item','end_document','finalize') or 'fixture' in node.name):
            details = {'name':node.name,'arguments':ast.unparse(node.args)}
            if node.name == 'make_sample':
                details['return_structures'] = [ast.unparse(n.value)[:2000] for n in ast.walk(node) if isinstance(n,ast.Return)]
            functions.append(details)
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('add_item','end_document','finalize','dumps'):
            if len(snippets) < 8:
                snippets.append({'file':name,'expression':ast.unparse(node)[:1000]})
    imports = sorted({n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module and n.module.startswith('PanGu')})
    contracts.append({'file':name,'functions':functions[:18],'imports':imports})

root = pathlib.Path.home() / 'work/pangu_validation/ma-0/8da52234-c88f-451d-95a1-fa33a978690b'
run_dir = root / ('broadcast-probe-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
run_dir.mkdir(parents=True)
(run_dir / 'sitecustomize.py').write_text(shim_source)
probe = run_dir / 'probe.py'
probe.write_text('import os,torch,torch.distributed as dist\n'
                 'rank=int(os.environ["LOCAL_RANK"])\ntorch.npu.set_device(rank)\ndist.init_process_group("hccl")\n'
                 'obj=[{"synthetic_probe":17} if rank==0 else None]\ndist.broadcast_object_list(obj,src=0)\n'
                 'assert obj==[{"synthetic_probe":17}]\n'
                 'v=torch.tensor([rank+1.0],device="npu")\ndist.all_reduce(v)\nassert v.item()==3.0\n'
                 'print("PROBE_OK_RANK_"+str(rank),flush=True)\ndist.destroy_process_group()\n')
env = os.environ.copy()
env['PYTHONPATH'] = str(run_dir) + os.pathsep + env.get('PYTHONPATH','')
env['ASCEND_RT_VISIBLE_DEVICES'] = '0,1'
env['OMP_NUM_THREADS'] = '1'
p = subprocess.Popen(['torchrun','--standalone','--nnodes=1','--nproc_per_node=2',str(probe)],
                     env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
try:
    output,_ = p.communicate(timeout=120)
except subprocess.TimeoutExpired:
    os.killpg(p.pid,signal.SIGTERM)
    try: output,_ = p.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid,signal.SIGKILL)
        output,_ = p.communicate(timeout=15)
(run_dir / 'probe.log').write_text(output)
ok = p.returncode == 0 and all('PROBE_OK_RANK_'+str(rank) in output for rank in range(2))
print(json.dumps({'fixture_contracts':contracts,'serialization_expressions':snippets,
                 'broadcast_probe':{'passed':ok,'exit_code':p.returncode,'run_directory':str(run_dir),
                                    'verified_ranks':[rank for rank in range(2) if 'PROBE_OK_RANK_'+str(rank) in output],
                                    'error_tail':output.splitlines()[-25:] if not ok else []}}))
if not ok:
    sys.exit(1)
