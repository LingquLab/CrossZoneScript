"""Check the intended 16-rank communication path before loading model weights."""
import json
import os
import torch
import torch_npu
import torch.distributed as dist

local_rank = int(os.environ['LOCAL_RANK'])
torch.npu.set_device(local_rank)
dist.init_process_group(backend='hccl')
rank, world = dist.get_rank(), dist.get_world_size()
assert world == 16
expected = world * (world + 1) / 2
checked = []
for size in [4, 4 * 1024**2, 96 * 1024**2]:
    value = torch.full((size // 4,), float(rank + 1), device=f'npu:{local_rank}', dtype=torch.float32)
    dist.all_reduce(value)
    torch.npu.synchronize()
    assert float(value[0].item()) == expected and float(value[-1].item()) == expected
    checked.append(size)
    del value
src = torch.full((16 * 1024**2 // 4,), float(rank + 1), device=f'npu:{local_rank}', dtype=torch.float32)
dst = torch.empty((src.numel() // world,), device=f'npu:{local_rank}', dtype=torch.float32)
dist.reduce_scatter_tensor(dst, src)
torch.npu.synchronize()
assert float(dst[0].item()) == expected and float(dst[-1].item()) == expected
gathered = torch.empty_like(src)
dist.all_gather_into_tensor(gathered, dst)
torch.npu.synchronize()
assert float(gathered[0].item()) == expected and float(gathered[-1].item()) == expected
dist.barrier()
print('GLM_HCCL_SMOKE=' + json.dumps({'rank': rank, 'world': world,
    'allreduce_bytes': checked, 'reduce_scatter_input_bytes': 16 * 1024**2,
    'expected': expected, 'completed': True}), flush=True)
dist.destroy_process_group()
