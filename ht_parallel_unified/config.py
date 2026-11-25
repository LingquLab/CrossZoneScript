"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T16_CASE = 'ht-parallel-ep64-t16-fp32-1abdfffd'
T32_CASE = 'ht-parallel-ep64-t32-fp32-cross-sq-stripe-cq-dcci-29af4e30'
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
GZIP_SHA256 = 'ea420c137d67ed1954e31ccb21625104d613785b84618876b66e3e063e01aa1c'
KERNEL_SHA256 = '29af4e30e8f427aaea105616e27494ced3b734df06a880cdefc290015c3e4f8d'
NAMES = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready',
         'returned', 'completed', 'output', 'status')
