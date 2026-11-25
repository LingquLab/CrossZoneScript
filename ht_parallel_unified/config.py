"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T16_CASE = 'ht-parallel-ep64-t16-fp32-1abdfffd'
T32_CASE = 'ht-parallel-ep64-t32-fp32-so-lane0-482fca4e'
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
GZIP_SHA256 = '7ff435834521589d5afdbd2e8461449e8d79a914d506816c9c2973a4eae1ef9a'
KERNEL_SHA256 = '482fca4e940250c33bbeb93ce0cc8bdec773e891a7fb3c3f6564c2cf00b48b6b'
NAMES = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready',
         'returned', 'completed', 'output', 'status')
SHARED_SO_QP = True
