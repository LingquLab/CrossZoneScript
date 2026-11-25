"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T32_CASE = 'ht-parallel-ep64-t32-fp32-1abdfffd'
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
GZIP_SHA256 = '27280cc5387686a70d00d14da7d17305750e6582872e342f012a5bf604fe84df'
KERNEL_SHA256 = '1abdfffda622579ec3327f092a3a3960ba9959c303b1f6653f1c9691c297ba28'
NAMES = ('gather', 'gather_ready', 'partial', 'ready', 'local_ready',
         'returned', 'completed', 'output', 'status')
