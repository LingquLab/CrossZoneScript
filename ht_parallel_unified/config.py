"""Shared shape and addresses for the EP64/H128 unified kernel workload."""
ENTRY = 'tilexr_ep_ht_combine_parallel_kernel'
CASE = 'ht-parallel-ep64-t4-fp32-1abdfffd'
TOKENS = 4
CHUNKS = 4
T8_CASE = 'ht-parallel-ep64-t8-fp32-1abdfffd'
T16_CASE = 'ht-parallel-ep64-t16-fp32-1abdfffd'
T32_CASE = 'ht-parallel-ep64-t32-fp32-1abdfffd'
T32_LONG_CASE = T32_CASE + '-long-wall'
T32_CROSS_STRIPE_CASE = 'ht-parallel-ep64-t32-fp32-99de129d-cross-stripe'
CROSS_STRIPE_GZIP_SHA256 = 'c29bb984ac4ef35fe8c53735526f021bd4d0df5232b35681ef4747afb9a14e22'
CROSS_STRIPE_KERNEL_SHA256 = '99de129d1b048def25f006e84c99b66b116fd14064164da72d1a44b71f9224e9'
T32_CQ_ORDERED_CASE = 'ht-parallel-ep64-t32-fp32-88733694-cq-ordered'
CQ_ORDERED_GZIP_SHA256 = '35a8dbfc1f5d10594b5b86425e9d279f4daf16da801b73dc2a45c8761a6b40b5'
CQ_ORDERED_KERNEL_SHA256 = '88733694f123a5156aade70429597728d2e003db51dfef19efbda792fedbd8f5'
T32_FP32_INIT_CASE = 'ht-parallel-ep64-t32-fp32-c1df0d77-fp32-init'
FP32_INIT_GZIP_SHA256 = '5328b2783059b713962e3ec48c323880f03fbd8e49620207651f382940bacc24'
FP32_INIT_KERNEL_SHA256 = 'c1df0d77c507bd76a7c8c5b277a31e241074c6aac7eaf78102c122f74c2baba2'
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
