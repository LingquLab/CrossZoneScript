from pathlib import Path
import hashlib
import shlex
import subprocess

out = Path(__file__).resolve().parent
previous = Path('/tmp/tilexr-ht-bs32.AbIjf4/end-to-end')
text = (previous / 'combine_sst_fp32.cpp').read_text()

def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new, 1)

replace('class DeviceTransport', r'''
enum StageId {
    Kernel, SendSetup, SendSubmit, SendDrain, LocalReady, RouteRead, PostBatch, Doorbell, SubmitSO, WaitCQ,
    ReduceSetup, ReduceTotal, WaitRemoteSO, ReduceRows, LoadPartial, AddPartial, StoreOutput, WriteStatus
};
class StageTrace {
public:
    static constexpr uint32_t Bytes = 16384;
    __aicore__ void Init(TPipe &pipe)
    {
        pipe.InitBuffer(buffer_, Bytes);
        data_ = buffer_.Get<uint64_t>();
    }
    __aicore__ void Mark(uint32_t event)
    {
        const auto cycle = static_cast<uint64_t>(AscendC::GetSystemCycle());
        if (cursor_ + 2 <= Bytes / sizeof(uint64_t)) {
            data_.SetValue(cursor_++, cycle);
            data_.SetValue(cursor_++, event);
        } else {
            ++lost_;
        }
    }
    __aicore__ void Export(GM_ADDR address)
    {
        data_.SetValue(0, cursor_);
        data_.SetValue(1, lost_);
        if (cursor_ % 4 != 0) {
            data_.SetValue(cursor_, 0);
            data_.SetValue(cursor_ + 1, 0);
        }
        GlobalTensor<uint64_t> target;
        target.SetGlobalBuffer(reinterpret_cast<__gm__ uint64_t *>(address));
        SetFlag<HardEvent::S_MTE3>(EVENT_ID1);
        WaitFlag<HardEvent::S_MTE3>(EVENT_ID1);
        DataCopyPad(target, data_, DataCopyExtParams(1, ((cursor_ + 3) & ~3U) * sizeof(uint64_t), 0, 0, 0));
        SetFlag<HardEvent::MTE3_S>(EVENT_ID1);
        WaitFlag<HardEvent::MTE3_S>(EVENT_ID1);
    }
private:
    TBuf<QuePosition::VECCALC> buffer_;
    LocalTensor<uint64_t> data_;
    uint32_t cursor_ = 2;
    uint32_t lost_ = 0;
};

class StageScope {
public:
    __aicore__ StageScope(StageTrace &trace, StageId id) : trace_(trace), event_(2 * id + 1)
    {
        trace_.Mark(event_);
    }
    __aicore__ ~StageScope() { trace_.Mark(event_ + 1); }
private:
    StageTrace &trace_;
    uint32_t event_;
};

class DeviceTransport''')
replace('uint32_t owner, TPipe &pipe)', 'uint32_t owner, TPipe &pipe, StageTrace &trace)')
replace('c_(config), owner_(owner)', 'c_(config), owner_(owner), trace_(trace)')
replace('    static constexpr uint32_t kBatchRows = 32;', '    StageTrace &trace_;\n    static constexpr uint32_t kBatchRows = 32;')
for name, stage in {
    'Range LoadRange(uint32_t peer, uint32_t slot, uint32_t chunk)': 'RouteRead',
    'int32_t LoadCount(uint32_t peer)': 'RouteRead',
    'Record LoadRecord(uint32_t peer, uint32_t row)': 'RouteRead',
    'uint32_t WaitReady(uint32_t chunk)': 'LocalReady',
    'uint32_t Notify(uint32_t peer, uint32_t lane, uint32_t chunk)': 'SubmitSO',
    'uint32_t PostPeerBatch(uint32_t peer, uint32_t lane, uint32_t rows)': 'PostBatch',
    'bool WaitWindow(uint32_t window, uint32_t first, uint32_t last)': 'WaitRemoteSO',
    'uint32_t ReduceTokens(uint32_t begin, uint32_t end)': 'ReduceRows',
}.items():
    old = f'__aicore__ {name}\n    {{'
    replace(old, old + f'\n        StageScope timing(trace_, {stage});')
replace('                const uint32_t quiet = TileXR::UDMAQuietStatusOnQp(args_, peer, qp);', '''                uint32_t quiet;
                {
                    StageScope timing(trace_, WaitCQ);
                    quiet = TileXR::UDMAQuietStatusOnQp(args_, peer, qp);
                }''')
replace('        TileXR::UDMARingDoorbell(head + count, context);', '''        {
            StageScope timing(trace_, Doorbell);
            TileXR::UDMARingDoorbell(head + count, context);
        }''')
replace('const SendConfig &config, TPipe &pipe)', 'const SendConfig &config, TPipe &pipe, StageTrace &trace)')
replace('output_(output), config_(config)', 'output_(output), config_(config), trace_(trace)')
replace('    GM_ADDR localReady_;', '    StageTrace &trace_;\n    GM_ADDR localReady_;')
replace('''            DataCopy(row, returned, hidden);
            Sync<HardEvent::MTE2_V>();
            Add(output, output, row, hidden);
            Sync<HardEvent::V_MTE2>();''','''            {
                StageScope timing(trace_, LoadPartial);
                DataCopy(row, returned, hidden);
                Sync<HardEvent::MTE2_V>();
            }
            {
                StageScope timing(trace_, AddPartial);
                Add(output, output, row, hidden);
                Sync<HardEvent::V_MTE2>();
            }''')
replace('        Sync<HardEvent::V_MTE3>();\n        GlobalTensor<float> result;', '        StageScope timing(trace_, StoreOutput);\n        Sync<HardEvent::V_MTE3>();\n        GlobalTensor<float> result;')
begin = text.index('    TPipe pipe;', text.index('extern "C" __global__'))
text = text[:begin] + '''    TPipe pipe;
    StageTrace trace;
    trace.Init(pipe);
    trace.Mark(2 * Kernel + 1);
    TBuf<QuePosition::VECCALC> queueStateScratch;
    trace.Mark(2 * SendSetup + 1);
    pipe.InitBuffer(queueStateScratch, 32);
    const uint32_t block = GetBlockIdx();
    DeviceTransport transport(commArgs, workspace, forward, counts, ranges, config, block, pipe, trace);
    trace.Mark(2 * SendSetup + 2);

    uint32_t sendResult;
    {
        StageScope timing(trace, SendSubmit);
        sendResult = Submit(config, block, transport);
    }
    {
        StageScope timing(trace, SendDrain);
        const uint32_t drained = Drain(transport);
        if (sendResult == Success) {
            sendResult = drained;
        }
    }
    TBuf<TPosition::VECCALC> statusBuffer;
    pipe.InitBuffer(statusBuffer, 32);
    auto local = statusBuffer.Get<uint32_t>();
    {
        StageScope timing(trace, WriteStatus);
        ExportStatus(local, status + block * 32, sendResult);
    }
    trace.Mark(2 * ReduceSetup + 1);
    FinalReduceWorker worker(workspace, serverMask, localReady, output, config, pipe, trace);
    trace.Mark(2 * ReduceSetup + 2);
    uint32_t reduceResult;
    {
        StageScope timing(trace, ReduceTotal);
        reduceResult = ReduceWindows(config, block, GetBlockNum(), worker);
        worker.Drain();
    }
    {
        StageScope timing(trace, WriteStatus);
        ExportStatus(local, status + GetBlockNum() * 32 + block * 32, reduceResult);
    }
    trace.Mark(2 * Kernel + 2);
    trace.Export(status + 4096 + uint64_t(block) * StageTrace::Bytes);
}
'''
source = out / 'combine_sst_fp32_profile.cpp'
source.write_text(text)
flags_path = Path('/tmp/tilexr-final-reduce-build/src/ep/CMakeFiles/tilexr_ep_ht_inter_server_send_kernel_tmp.dir/flags.make')
lines = flags_path.read_text().splitlines()
flags = [arg for line in lines if line.startswith(('CCE_DEFINES =', 'CCE_INCLUDES =', 'CCE_FLAGS ='))
         for arg in shlex.split(line.split('=', 1)[1])]
compiler = Path(next(line.split('with ', 1)[1] for line in lines if line.startswith('# compile CCE with ')))
original_include = '/data/dhp/TileXR-combine-ht-final-reduce/src/ep/combine_ht'
unlinked = out / 'unlinked.o'
obj = out / 'inter_server_send.o'
subprocess.run([compiler, '-I' + str(previous / 'override/include'), '-I' + original_include, '-xcce',
                *flags, '-DTILEXR_EP_HT_FINAL_REDUCE_SST_FP32', '-c', source, '-o', unlinked], check=True)
subprocess.run([compiler.with_name('ld.lld'), '-m', 'aicorelinux', '-Ttext=0', unlinked,
                '--static', '-o', obj, '--allow-multiple-definition'], check=True)
with obj.open('r+b') as dest:
    dest.truncate(10485760)
print(hashlib.sha256(obj.read_bytes()).hexdigest())
