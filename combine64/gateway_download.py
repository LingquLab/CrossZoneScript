"""Download and verify the requested GatewayReduce Perfetto traces on green Windows."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from bootstrap_green import REVISIONS, SESSION
from transport import connect

REVISION = REVISIONS['4d37ae6']
ROOT = PurePosixPath('/mnt/share/tilexr-sim-jobs') / SESSION / 'run-4d37ae6-host18-a1'
DESTINATION = Path(r'D:\00_项目\02_moonep_URAM\test 1128 gateway规约')
TRACE = 'profiling/gateway_perfetto.json'
FILES = ('phase-result.json', 'profiling-summary.json', TRACE)
CASE_NAME = re.compile(r'cores(1|8|16|24|32)-chunk(16|32|64|128|512)-a([1-9]|1[0-9]|20)')


def verify(directory, label):
    row = json.loads((directory / 'phase-result.json').read_text(encoding='utf-8'))
    profile = json.loads((directory / 'profiling-summary.json').read_text(encoding='utf-8'))
    path = directory / TRACE
    assert row['case'] == label and row['candidate_commit'] == REVISION and row['status'] == 'sst_and_checker_passed'
    assert row['checked_ranks'] == 64 and row['profiling_dropped_records'] == 0
    assert row['port0_die_histogram'] == {'0': 32, '1': 32}
    assert row['profiling_lanes'] == (1 if row['cores'] == 1 else 64 * row['cores'])
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
    assert digest.hexdigest() == row['trace_sha256'] == profile['trace_sha256']
    trace = json.loads(path.read_text(encoding='utf-8'))
    assert sum(event['name'] == 'Combine-HT GatewayReduce' for event in trace['traceEvents']) == row['profiling_lanes']
    return row, dict(sha256=digest.hexdigest(), bytes=path.stat().st_size)


def download(sftp, label):
    assert CASE_NAME.fullmatch(label), label
    target = DESTINATION / label
    if target.is_dir():
        return verify(target, label)
    with tempfile.TemporaryDirectory(dir=DESTINATION, prefix='.' + label + '-') as tmp:
        temporary = Path(tmp) / label
        for name in FILES:
            remote = str(ROOT / 'cases' / label / name)
            local = temporary / name
            local.parent.mkdir(parents=True, exist_ok=True)
            with sftp.open(remote, 'rb') as source, local.open('xb') as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
        evidence = verify(temporary, label)
        temporary.rename(target)
    return evidence


def report(rows, evidence, incomplete=None):
    matrix = [row for row in rows if row['cores'] != 1]
    micro = [row for row in rows if row['cores'] == 1]
    assert {(r['cores'], r['chunk_tokens']) for r in matrix} == {
        (core, chunk) for core in (8, 16, 24, 32) for chunk in (16, 32, 64, 128)}
    expected_micro = {16, 32, 64, 128} if incomplete else {16, 32, 64, 128, 512}
    assert {row['chunk_tokens'] for row in micro} == expected_micro
    assert len(matrix) == 16 and len(micro) == len(expected_micro)
    result = [
        '# Combine-HT Gateway 规约仿真',
        '',
        f'18 服务器，TileXR `{REVISION}`，CANN 9.1.0，V310 ScalarUnitL1，`SIM_SEND=ON`。',
        '64P 随机路由采用每 rank BS 128、topk 6、hidden 128、256 experts、FP32，固定随机种子 20261124。',
        '每组 8 个 rank 的前四个 rank 的 Port 0 绑定 Die 1，后四个绑定 Die 0。',
        '提前写好专家输出、gather 数据和 ready 标记，计时从单独启动 Gateway 规约开始。',
        '算子耗时取所有活动 rank 和 AIV 的最大 Begin–End 跨度。周期按 1,650 cycle/µs 换算。',
        'SST 墙钟从 SST 进程启动计到退出，用例墙钟还包括输入准备、数值校验和 trace 导出。',
        '',
        '## 64P 随机路由',
        '',
        '| 规约核数 | Chunk | SST 模拟（µs） | 算子（µs） | SST 墙钟（s） | 用例墙钟（s） |',
        '| ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for row in sorted(matrix, key=lambda current: (current['cores'], current['chunk_tokens'])):
        result.append(f"| {row['cores']} | {row['chunk_tokens']} | {row['platform_simulated_us']:.3f} | "
                      f"{row['profiling_operator_us']:.3f} | {row['sst_wall_seconds']:.3f} | {row['runner_wall_seconds']:.3f} |")
    result += [
        '',
        '## 单核固定六份贡献',
        '',
        '只有 rank 0 的一个 AIV 执行规约。每个 token 的六份贡献都在本机，其中两份在 Gateway rank，四份来自同机其他 rank。',
        '每个配置跑 4 个完整 chunk，按 StageReady 至 WorkEnd 的跨度求平均。',
        '',
        '| Chunk | 样本（µs） | 平均（µs） | SST 模拟（µs） | 算子（µs） | SST 墙钟（s） | 用例墙钟（s） |',
        '| ---: | --- | ---: | ---: | ---: | ---: | ---: |',
    ]
    for row in sorted(micro, key=lambda current: current['chunk_tokens']):
        samples = ' / '.join(f'{sample:.3f}' for sample in row['chunk_sample_us'])
        result.append(f"| {row['chunk_tokens']} | {samples} | {row['mean_chunk_us']:.3f} | "
                      f"{row['platform_simulated_us']:.3f} | {row['profiling_operator_us']:.3f} | "
                      f"{row['sst_wall_seconds']:.3f} | {row['runner_wall_seconds']:.3f} |")
    if incomplete:
        result.append('| 512 | — | — | — | — | — | — |')
        result += ['', incomplete['description']]
    if all(row.get('profiling_detail') == 'stages' for row in micro):
        result += ['', '单核结果统一使用阶段级 profiling。首轮逐 token 打点的 chunk 128 和 512 达到墙钟超时，未生成输出 dump。']
    result += [
        '',
        '## 运行记录',
        '',
        '首次启动在进入 SST 前因容器上下文管理器缺少装饰器而退出，修正编排后通过首个 64P 用例。',
        '矩阵执行中 SSH 中断。复查 18 的进程与已落盘结果后，只续跑缺少通过记录的组合。',
    ]
    result += ['', '每个用例目录中的 `profiling/gateway_perfetto.json` 可在 Perfetto 中打开。',
               '`results.json` 保存逐项耗时、校验结果和 trace SHA-256。', '']
    for name, content in (
            ('report.md', '\n'.join(result)),
            ('results.json', json.dumps(dict(cases=rows, traces=evidence, incomplete=incomplete), ensure_ascii=False, indent=2) + '\n')):
        target = DESTINATION / name
        partial = target.with_suffix(target.suffix + '.tmp')
        partial.write_text(content, encoding='utf-8')
        partial.replace(target)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--args-file', type=Path, required=True)
    args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
    assert DESTINATION.anchor and Path(DESTINATION.anchor).is_dir()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    evidence = {}
    rows = []
    incomplete = None
    optional = args.get('optional_chunk512')
    with connect('18', 'password') as client, client.open_sftp() as sftp:
        sftp.get_channel().settimeout(120)
        for label in args['cases']:
            row, evidence[label] = download(sftp, label)
            rows.append(row)
        if optional:
            assert optional == 'cores1-chunk512-a2'
            path = ROOT / 'cases' / optional / 'phase-result.json'
            try:
                with sftp.open(str(path), 'rb') as stream:
                    latest = json.load(stream)
            except FileNotFoundError:
                latest = {'status': 'pending'}
            state = latest['status']
            if state == 'sst_and_checker_passed':
                row, evidence[optional] = download(sftp, optional)
                rows.append(row)
            else:
                incomplete = {'chunk_tokens': 512, 'attempt': optional, 'status': state}
                if 'sst_wall_seconds' in latest:
                    incomplete['sst_wall_seconds'] = latest['sst_wall_seconds']
                if state == 'timed_out':
                    incomplete['description'] = f"chunk 512 的阶段级复测在 SST 墙钟 {latest['sst_wall_seconds']:.3f} s 后超时。"
                elif state == 'pending':
                    incomplete['description'] = 'chunk 512 的阶段级复测在出具报告时尚无终态。首轮逐 token 打点在 SST 墙钟 2711.482 s 后超时。'
                else:
                    incomplete['description'] = f'chunk 512 的阶段级复测状态为 `{state}`。'
    if args.get('report', False):
        report(rows, evidence, incomplete)
    print(json.dumps(dict(directory=str(DESTINATION), cases=evidence, report_written=args.get('report', False), incomplete=incomplete, single_core=[{key: row[key] for key in ('chunk_tokens', 'mean_chunk_us', 'chunk_sample_us', 'platform_simulated_us', 'profiling_operator_us', 'sst_wall_seconds', 'runner_wall_seconds')} for row in rows if row['cores'] == 1])))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'error_class': type(error).__name__, 'detail': str(error)[:250]}, ensure_ascii=True))
        raise SystemExit(1)
