"""Process-local compatibility for NPU transfer mode with new PyTorch c10d."""
import torch
import torch_npu
from torch_npu.contrib import transfer_to_npu  # Apply the installed NPU transfer hooks first.

_original_capability = torch.cuda.get_device_capability


def _capability_without_cuda_fastpaths(*args, **kwargs):
    capability = _original_capability(*args, **kwargs)
    # The installed NPU stub returns None. NPU must take generic c10d allocation,
    # rather than CUDA architecture-specific NVLS allocation.
    return (0, 0) if capability is None else capability


torch.cuda.get_device_capability = _capability_without_cuda_fastpaths
