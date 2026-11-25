"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T16_CASE = 'ht-parallel-ep64-t16-fp32-1abdfffd'
T32_CASE = 'ht-parallel-ep64-t32-fp32-mte-cqe-f9b5f8c1'
T32_LONG_CASE = T32_CASE + '-long-wall'
HIDDEN = 128
ROW = 512
MAGIC = 2026
BLOCKS = 56
PRODUCER_QUEUE = (16, 36, 17, 37, 18, 38, 19, 39)
EXTRA = {
    'fullmesh_info': 0x300080000, 'fullmesh_sq': 0x317000000,
    'fullmesh_cq': 0x318000000, 'fullmesh_mem': 0x319000000,
    'masks': 0x404000000, 'prefill': 0x405000000, 'output': 0x406000000,
    'server_mask': 0x407000000, 'return_forward': 0x408000000,
}
GZIP_SHA256 = '2691346847227d7ff096eae8b7e5f4f49e2a5251bd827eeed91f07071a965a5e'
KERNEL_SHA256 = 'f9b5f8c16f503710044087aea52950bd7d7af1485e658dee6b6b89a221ed741e'
NAMES = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready',
         'returned', 'completed', 'output', 'status')
