"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T16_CASE = 'ht-parallel-ep64-t16-fp32-1abdfffd'
T32_CASE = 'ht-parallel-ep64-t32-fp32-cross-sq-stripe-99de129d'
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
GZIP_SHA256 = 'c29bb984ac4ef35fe8c53735526f021bd4d0df5232b35681ef4747afb9a14e22'
KERNEL_SHA256 = '99de129d1b048def25f006e84c99b66b116fd14064164da72d1a44b71f9224e9'
NAMES = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready',
         'returned', 'completed', 'output', 'status')
