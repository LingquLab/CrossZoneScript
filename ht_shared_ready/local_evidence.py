"""Count status, ready, and queue activity from a retained local_return case."""
import collections
import re

GM_BYTES = 0x2000000000
TRACE_OBJECT = re.compile(rb'\{[^{}]*"(?:wrAddr_|rdAddr_|oriAddr_)"[^{}]*\}')
TRACE_LIMIT = 16 * 1024 * 1024
SQ_RING = 16384 * 64


def trace_address(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def queue_addresses(context, host):
    addresses = {}
    sq, cq = set(), set()
    for peer in range(host // 8 * 8, host // 8 * 8 + 8):
        if peer == host:
            continue
        qp = peer % 8
        offset = (peer * 32 + qp) * 80
        for name, field in (('sq_head',24), ('doorbell',48), ('wqeCount',64)):
            address, = struct.unpack_from('<Q', context, offset+field)
            addresses[address % GM_BYTES] = name
        sq.add(struct.unpack_from('<Q', context, offset+8)[0] % GM_BYTES)
        slot = peer*40+(qp%2)*20+qp//2
        cq.add(0x1a00000000+slot*SQ_RING)
    return addresses, sq, cq


def count_queue_trace(path, addresses, sq, cq):
    with path.open('rb') as file:
        raw = file.read(TRACE_LIMIT)
        truncated = bool(file.read(1))
    counts = collections.Counter()
    for match in TRACE_OBJECT.finditer(raw):
        try:
            item = json.loads(match.group())
            event = item.get('busPayloadType_')
            coordinates = {trace_address(item[field]) % GM_BYTES for field in ('wrAddr_','rdAddr_','oriAddr_')
                           if field in item and item[field] is not None}
        except (KeyError, ValueError, TypeError):
            continue
        names = {addresses[a] for a in coordinates if a in addresses}
        counts.update(name+'_writes' for name in names if event=='WR_REQ')
        if any((a-0x1900000000)//SQ_RING*SQ_RING+0x1900000000 in sq for a in coordinates):
            if event=='RD_REQ':
                counts['sq_fetch'] += 1
            if event=='WR_REQ':
                counts['sq_writes'] += 1
        if event=='WR_REQ' and any((a-0x1a00000000)//SQ_RING*SQ_RING+0x1a00000000 in cq for a in coordinates):
            counts['cq_writes'] += 1
    counts['trace_files'] = 1
    counts['trace_truncated'] = int(truncated)
    return counts


def read_local_evidence(case):
    status_hist, per_rank = collections.Counter(), collections.Counter()
    ready_hist, trace = collections.Counter(), collections.Counter()
    first_error = first_status = first_ready = None
    for rank in range(64):
        label = f'rank{rank:02d}'
        actual = case/'actual'/label
        status = actual/'status.bin'
        if status.is_file():
            data = status.read_bytes()
            assert len(data) == 256
            words = [struct.unpack_from('<i',data,owner*32)[0] for owner in range(8)]
            status_hist.update(words)
            per_rank[(sum(code != 0 for code in words), words.count(-1))] += 1
            if rank == 0:
                first_status = words
            if first_error is None:
                first_error = next(([rank,i,code] for i,code in enumerate(words) if code), None)
        completed = actual/'completed.bin'
        if completed.is_file():
            data = completed.read_bytes()
            assert len(data) == 4096
            flags = [struct.unpack_from('<q',data,owner*512)[0] for owner in range(8)]
            ready_hist.update(flags)
            if rank == 0:
                first_ready = flags
        path = case/'OUTPUT/stat_trace'/f'Host{rank}_ba_layer_trace.json'
        context = case/'input_bin'/label/'native_sq_ctx.bin'
        if path.is_file() and context.is_file():
            trace.update(count_queue_trace(path,*queue_addresses(context.read_bytes(),rank)))
    layout = json.loads((case/'export-layout.json').read_text()) if (case/'export-layout.json').is_file() else {}
    exported_fields = sorted(layout['ranks'][0]['exports']) if layout.get('ranks') else []
    result = dict(status_hist=status_hist,
                  ranks_by_nonzero_and_sentinel=[[a,b,n] for (a,b),n in sorted(per_rank.items())],
                  rank0_status_i32=first_status, first_nonzero=first_error,
                  localReady_hist=ready_hist, rank0_localReady=first_ready,
                  ba={field:trace[field] for field in ('trace_files','trace_truncated','sq_head_writes',
                                                      'wqeCount_writes','doorbell_writes','sq_writes',
                                                      'sq_fetch','cq_writes')},
                  exported_fields=exported_fields)
    if (case/'sim.log').is_file():
        result['cqe'] = (case/'sim.log').read_bytes().count(b'WrCQEResp')
    return result
