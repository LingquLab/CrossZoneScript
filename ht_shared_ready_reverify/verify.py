"""Verify production-revision kernel objects through a fresh SST case chain."""
PASS = 'sst_and_checker_passed'


def byte_handoff(stage, abis):
    """Check both handoff ranges against the actual input of the consuming ELF."""
    source = stage_path('attached')
    destination = stage_path(stage)
    layout = json.loads((source/'export-layout.json').read_text())['ranks']
    workload = json.loads((destination/'workload.yml').read_text())['workload_config']
    totals = {}
    for field, offset_key, size in (('output', 'partialOffset', 2048), ('completed', 'readyOffset', 4096)):
        digest = hashlib.sha256()
        for row in layout:
            rank = row['rank']
            name = f'rank{rank:02d}'
            config = {item['name'].split('.')[-1]:item['value'] for item in abis[rank]['fields']}
            assert config['reducerCount'] == 8
            old = row['exports'][field]
            workspace = workload[f'chip{rank}']['kernel_param']['load_workspace']['value']
            assert old['addr'] == workspace + config[offset_key] and old['size'] == size
            with (destination/'input_bin'/name/'workspace.bin').open('rb') as file:
                file.seek(config[offset_key])
                loaded = file.read(size)
            actual = (source/'actual'/name/(field+'.bin')).read_bytes()
            assert loaded == actual and len(actual) == size
            if field == 'completed':
                for start in range(0, size, 512):
                    assert struct.unpack_from('<qii', loaded, start) == (config['magic'], 4, 0)
            digest.update(loaded)
        totals[field] = dict(bytes=64*size, sha256=digest.hexdigest())
    totals['ready_slots_per_rank'] = 8
    return totals


def condensed(report):
    result = {field:report[field] for field in ('case','status','sim_us','cqe','checked','first_mismatch',
                                              'kernel_sha256','runner_exit_code') if field in report}
    if report.get('exports_sha256'):
        result['output_sha256'] = report['exports_sha256']['output']
    if report.get('diagnostics'):
        result['owner_status'] = report['diagnostics']['status_hist']
    return result


def reverify(manifest, attached_request, return_request, fixed):
    global STEM, REVISION
    REVISION = manifest['revision']
    STEM = 'ht-reverify-t4-3bfa9fe5-r1'
    stages = ('attached','inter_server_send','local_return','final_reduce')
    assert not any(stage_path(stage).exists() for stage in stages)
    report = dict(revision=REVISION, tree=manifest['tree'])
    report['attached'] = condensed(attached_case(attached_request))
    if report['attached'].get('status') != PASS:
        return report
    for stage in ('inter_server_send', 'local_return'):
        return_request['stages'] = [stage]
        report[stage] = condensed(run_downstream(return_request)['phases'][0])
        if stage == 'inter_server_send' and report[stage].get('status') != PASS:
            return report
    abis = json.loads(gzip.decompress(base64.b64decode(attached_request['abis'], validate=True)))
    report['sender_handoff'] = byte_handoff('inter_server_send', abis)
    if report['local_return'].get('status') != 'failed' or report['local_return'].get('owner_status') != {0:64, 3:448}:
        return report

    REVISION = manifest['fixed_revision']
    report['fixed_revision'] = REVISION
    return_request['objects']['local_return'] = fixed
    return_request['hashes']['local_return'] = manifest['object_sha256']['local_return_fixed']
    return_request.update(scratch128=True, stages=['local_return','final_reduce'])
    report['scratch128'] = [condensed(phase) for phase in run_downstream(return_request)['phases']]
    if report['scratch128'][0].get('status') == PASS:
        report['local_return_handoff'] = byte_handoff('local_return', abis)
    return report
