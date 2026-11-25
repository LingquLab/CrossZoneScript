"""Map firmware marker IDs to role, function, and timing semantics."""

ROLE_NAMES = ('Gather', 'Gateway Reduce', 'Local Return', 'Cross Return', 'Final Reduce')
ROLE_BOUNDS = (0, 8, 16, 24, 40, 56)

# (begin ID, end ID, Perfetto name, measured work, function, optional detail)
MARKERS = {
    'Gather': (
        (1, 2, 'gather_total', '机内 Gather 从进入角色到状态写回', 'RunCombineHT', ''),
        (18, 19, 'gather_chunk', '读取 Prefill 目录并提交一个 chunk 的 payload 与 SO', 'LocalUrmaSend::RunPrefilled', ''),
        (3, 5, 'gather_payload', '组装与发布当前 chunk 的 payload WQE', 'LocalUrmaSend::RunPrefilled', ''),
        (5, 6, 'gather_window_so', '提交 Gather 完成 SO WQE', 'LocalUrmaSend::RunPrefilled', ''),
        (24, 25, 'gather_prefill_load', 'MTE2 从 GM 读取预置 WQE 到 UB', 'LocalPutBatch::Flush', ''),
        (26, 27, 'gather_prefill_patch', 'Scalar 在 UB 更新 WQE 的队列字段', 'LocalPutBatch::Flush', ''),
        (16, 17, 'gather_sq_submit', 'MTE3 写 SQ、更新队列状态并敲 doorbell', 'LocalPutBatch::Flush', ''),
        (7, 8, 'gather_quiet', '发送末尾轮询 CQ 并回收 Gather 已发布的 WQE', 'LocalUrmaSend::RunPrefilled', ''),
    ),
    'Gateway Reduce': (
        (1, 2, 'gateway_reduce_total', 'Gateway 归约从进入角色到状态写回', 'RunCombineHT', ''),
        (3, 4, 'gateway_wait_gather', '轮询该 chunk / group 所需的 Gather SO', 'GatewayReduce::Run', ''),
        (4, 4200, 'gateway_reduce_partial', '读取各贡献行、局部归约并等 partial 写回完成', 'GatewayReduce::Run', 'chunk_end'),
        (22, 23, 'gateway_reduce_token', '读取并累加一行 token 的贡献，提交 partial 写回', 'ReduceGateway', ''),
        (4200, 4201, 'gateway_publish_ready', 'MTE3 发布当前 reducer 的共享 magic 与 epoch', 'GatewayReduce::Run', 'chunk'),
    ),
    'Local Return': (
        (4100, 4101, 'local_return_total', '同机回传从进入角色到状态写回', 'LocalReturnDevice::Run', ''),
        (4102, 4103, 'local_return_wait_ready', '等待该 chunk 的 Gateway partial 就绪', 'LocalReturn::Run', 'chunk'),
        (4104, 4105, 'local_return_post_payload', '构造并向同机对端提交一条 payload WQE', 'LocalReturn::Add', 'token'),
        (4106, 4107, 'local_return_self_copy', 'MTE2 读取本地 partial 后以 MTE3 写入本机 returned', 'LocalReturn::Add', 'token'),
        (4108, 4109, 'local_return_window_signal', '清理待完成的 payload 后发布同机窗口 ready，含 self-copy 写标记', 'LocalReturn::PublishWindow', 'window'),
        (4110, 4111, 'local_return_wait_cq', '对有未完成请求的同机 QP 敲待发门铃并轮询 CQ', 'LocalReturn::Drain', ''),
    ),
    'Cross Return': (
        (4300, 4301, 'cross_return_total', '跨机回传从进入角色到状态写回', 'InterServerSendDevice::Run', ''),
        (4302, 4303, 'cross_return_wait_ready', '等待该 chunk 的 Gateway partial 就绪', 'DeviceTransport::WaitReady', 'chunk'),
        (4304, 4305, 'cross_return_post_batch', '按对端组装 payload WQE，按 SQ 边界发布并回收 CQ', 'DeviceTransport::PostPeerBatch', 'peer,lane,batch_rows'),
        (4306, 4307, 'cross_return_payload_wait_cq', '等待当前 SQ 段的 payload WQE 完成并回收 CQ', 'DeviceTransport::PostPeerBatch', 'peer,lane,piece_wqes'),
        (4308, 4309, 'cross_return_window_so', '向指定对端和 lane 发布窗口 SO，包含 SO CQ 回收', 'DeviceTransport::Notify', 'peer,lane,chunk'),
        (4310, 4311, 'cross_return_so_wait_cq', '轮询并回收窗口 SO 所属 QP 的 CQ', 'DeviceTransport::Notify', 'peer,lane,chunk'),
        (4312, 4313, 'cross_return_drain', '遍历使用过的 QP 并确认待完成请求已回收', 'DeviceTransport::Drain', ''),
    ),
    'Final Reduce': (
        (4400, 4401, 'final_reduce_total', '最终归约从进入角色到状态写回', 'FinalReduceDevice::Run', ''),
        (4402, 4403, 'final_wait_window', '等待本机 localReady 以及远端两条 lane 的窗口 SO', 'FinalReduceWorker::WaitWindow', 'window'),
        (4404, 4405, 'final_reduce_token', '从 returned 读取贡献、FP32 累加并写回一个 token', 'FinalReduceWorker::ReduceFp32Token', 'token'),
        (4406, 4407, 'final_load_partial', 'MTE2 读取指定 server 的 FP32 partial 并等待搬运完成', 'FinalReduceWorker::ReduceFp32Token', 'token,server'),
        (4408, 4409, 'final_add_partial', 'Vector Add 累加第二份及后续贡献行', 'FinalReduceWorker::ReduceFp32Token', 'token,server'),
        (4410, 4411, 'final_write_output', 'MTE3 写回该 token 的输出并等待写回完成', 'FinalReduceWorker::ReduceFp32Token', 'token'),
    ),
}

LOCAL_EVENTS = dict(enumerate((
    'Begin', 'End', 'StageBegin', 'StageReady', 'WorkEnd', 'SoSubmit', 'QuietBegin',
    'QuietEnd', 'Batches', 'Tokens', 'Contributions', 'WqeBuildBegin', 'WqeBuildEnd',
    'SingleWqeBegin', 'SingleWqeEnd', 'SqSubmitBegin', 'SqSubmitEnd', 'ChunkSendBegin',
    'ChunkSendEnd', 'ReduceBegin', 'FirstTokenReady', 'TokenReady', 'TokenReduced',
    'PrefillLoadBegin', 'PrefillLoadEnd', 'PrefillPatchBegin', 'PrefillPatchEnd',
), 1))

POINTS = {
    5: ('阶段写回或 payload 工作结束', 'GatewayReduce::Run / LocalUrmaSend::RunPrefilled'),
    6: ('Gather SO 已提交', 'LocalUrmaSend::RunPrefilled'),
    9: ('一批 Gather payload WQE 已提交', 'LocalUrmaSend::RunPrefilled'),
    10: ('开始处理有贡献的 token', 'ReduceGateway'),
    11: ('处理一份本地或 Gather 贡献', 'ReduceGateway'),
    20: ('Gateway 初始化了 UB 后进入归约角色', 'GatewayReduce::Run'),
    21: ('当前 AIV 首个 token 就绪', 'ReduceGateway'),
}

SOURCE_FILES = ('combine_ht_kernel.h', 'local_return_worker.h', 'inter_server_send_worker.h', 'final_reduce_worker.h')
ROLE_SOURCE = dict(zip(ROLE_NAMES, (SOURCE_FILES[0], *SOURCE_FILES)))
