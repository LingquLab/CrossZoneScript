"""Stage a concrete two-node model configuration without starting the service."""
import hashlib
import json
from pathlib import Path
import socket

HOSTS = ['141.61.94.58', '141.61.94.26']
host = ARGS['host']
rank = HOSTS.index(host)
ip = ['173.27.3.58', '173.27.3.26'][rank]
root = Path('/home/glm16-09b20820')
root.mkdir(exist_ok=True)
written = {}
for name, data in FILES.items():
    assert name in {'benchmark.py', 'serve.py'}
    (root / name).write_text(data)
    written[name] = hashlib.sha256(data.encode()).hexdigest()
environment = {'ASCEND_RT_VISIBLE_DEVICES': '0,1,2,3,4,5,6,7',
    'HCCL_OP_EXPANSION_MODE': 'AIV', 'HCCL_BUFFSIZE': '2048',
    'HCCL_CONNECT_TIMEOUT': '600', 'HCCL_EXEC_TIMEOUT': '600',
    'VLLM_HOST_IP': ip, 'HCCL_IF_IP': ip, 'HCCL_SOCKET_IFNAME': 'data0.173',
    'GLOO_SOCKET_IFNAME': 'data0.173', 'TP_SOCKET_IFNAME': 'data0.173',
    'PYTORCH_NPU_ALLOC_CONF': 'expandable_segments:True', 'TASK_QUEUE_ENABLE': '1',
    'VLLM_ASCEND_ENABLE_FLASHCOMM1': '1', 'VLLM_ASCEND_ENABLE_PREFETCH_MLP': '1',
    'VLLM_WORKER_MULTIPROC_METHOD': 'spawn', 'PYTHONHASHSEED': '0'}
argv = ['vllm', 'serve', '/model', '--served-model-name', 'glm-5.2',
    '--host', ip, '--port', '8016', '--data-parallel-size', '1',
    '--tensor-parallel-size', '16', '--enable-expert-parallel',
    '--nnodes', '2', '--node-rank', str(rank), '--master-addr', '173.27.3.58',
    '--master-port', '29651', '--distributed-executor-backend', 'mp',
    '--data-parallel-backend', 'mp', '--max-model-len', '135000',
    '--max-num-batched-tokens', '16384', '--max-num-seqs', '20',
    '--gpu-memory-utilization', '0.9', '--enable-prefix-caching',
    '--trust-remote-code', '--quantization', 'ascend', '--kv-cache-dtype', 'fp8',
    '--attention-config', json.dumps({'indexer_kv_dtype': 'fp8'}),
    '--compilation-config', json.dumps({'mode': 3, 'cudagraph_mode': 'FULL_AND_PIECEWISE',
                                       'cudagraph_capture_sizes': [16, 8192]}),
    '--additional-config', json.dumps({'enable_flashcomm1': True, 'enable_cpu_binding': True,
                                       'multistream_overlap_shared_expert': True, 'enable_dsa_cp': True}),
    '--enable-auto-tool-choice', '--tool-call-parser', 'glm47', '--reasoning-parser', 'glm47']
argv += ['--api-server-count', '1'] if rank == 0 else ['--headless']
spec = {'host': host, 'node_rank': rank, 'environment': environment, 'argv': argv,
        'image': 'sha256:5588559036095d339c1cfb48c16d0227b841355490d8c0cb4e6730759f9c6674',
        'model': '/mnt/share/GLM-5.2', 'experiment': str(root),
        'name': f'glm16-09b20820-node{rank}', 'benchmark': {'input': 8192, 'output': 128,
        'concurrency': 1, 'warmup': 1, 'measured': 4},
        'runtime_difference': 'vLLM 0.32 / CANN 9.2.1; reference used vLLM 0.30 / CANN 9.2.1'}
(root / 'launch-spec.json').write_text(json.dumps(spec, indent=2))
print(json.dumps({'host': host, 'node_rank': rank, 'spec': str(root / 'launch-spec.json'),
                  'staged_files': written, 'service_started': False}))
