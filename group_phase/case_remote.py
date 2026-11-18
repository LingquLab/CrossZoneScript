"""在新目录校验64rank数据及4096阶段记录，原始记录留在容器内。"""
import json
from pathlib import Path
import sys

root, spec = Path(sys.argv[1]), json.loads(sys.argv[2])
source = root/'source'; sys.path.insert(0, str(source/'tools/unitest'))
from controller import memory_sst as mem, direct_group_clock as clock, direct_group_profile as profile
from controller.case import load_case

build = json.loads((root/('build-'+spec['mode']+'.json')).read_text())
revision = build['runtime_commit']; output = root/'cases'/spec['label']
suite = Path('/opt/sim/simulator/Testcases')/('phase_'+revision[:8]+'_'+spec['label'])
assert not suite.exists() and not output.exists(), 'preserve prior results'
output.mkdir(parents=True)
total = dict(small=32768, **{'512k': 524288, '4m': 4194304})[spec['size']]
raw = json.loads((source/'tools/unitest/cases/alltoall-mem-two-group.json').read_text())
raw.update(world_size=64, elements_per_peer=total//64//4, id='phase-'+spec['label'])
case_path = output/'case.json'; case_path.write_text(json.dumps(raw))
case = load_case(case_path, list(range(64)))
cfg = dict(schema='tilexr.unitest.sst.memory.v1', rank_size=64, suite_output=str(suite),
    build_report=str(root/('build-'+spec['mode']+'.json')), environment_report=str(root/'environment-report.json'),
    threads=64, simulation_timeout=900, iterations=1,
    clock=clock.identity_schedule(spec['target'], max(20000, spec['target']*4), 1000000000))
(output/'config.json').write_text(json.dumps(cfg, indent=2))
cfg = mem.load_config(output/'config.json', cfg)
report = mem.run(cfg, case, dict(mode='single', profiling=dict(enabled=False)), {}, output)
receipt = json.loads((suite/'process_receipt.json').read_text()) if (suite/'process_receipt.json').exists() else {}
phase = report.get('phase_profile')
diagnostic_error = None
if phase is None and receipt.get('returncode') == 0:
    try:
        import yaml
        chips = yaml.safe_load((suite/'v310_config/workload_config.yml').read_text())['workload_config']
        ranks=[]; payload_ranks=0
        for rank in range(64):
            prefix=mem.guarded_output(case, rank); initial=prefix+clock.initial(cfg['clock'], rank)
            chip=chips['chip%d'%rank]; dump=chip['output_param']['output']
            actual=mem.merge_die_dumps([suite/'bin'/('david_%d_die%d_output.bin'%(rank,die)) for die in (1,2)],
                dump['addr']-chip['sys_va_base_addr'], initial)
            payload_ranks+=actual[:len(prefix)]==mem.guarded_output(case,rank,True)
            ranks.append(dict(phase_records=profile.decode(actual[len(prefix):],cfg['clock'],rank,
                spec['mode'],require_on_time=False)))
        phase=profile.summarize(ranks,cfg['clock'],spec['mode'])
        phase['diagnostic_payload_validated_ranks']=payload_ranks
    except (ValueError,OSError,KeyError) as exc:
        diagnostic_error=str(exc)[:200]
row=dict(runtime_commit=revision, kernel_sha256=build['kernel']['sha256'], mode=spec['mode'],
    size=spec['size'], target=spec['target'], total_bytes_per_rank=total, bytes_per_peer=total//64,
    validated=report['status']=='passed' and len(report.get('operator_result',{}).get('ranks',[]))==64,
    simulated_us=report.get('phases',{}).get('simulation',{}).get('simulated_us'),
    phase_profile=phase, diagnostic_error=diagnostic_error, error=report.get('error'),
    failed_phase=report.get('failed_phase'), returncode=receipt.get('returncode'),
    remaining_processes=receipt.get('remaining_processes'), report=str(output/'result.json'))
(output/'summary.json').write_text(json.dumps(row,indent=2))
if phase:
    compact=dict(phase);compact.pop('ranks',None)
    compact['groups']={g:{stage:{k:v for k,v in stats.items() if k in ('median','mean','max')}
        for stage,stats in stages.items()} for g,stages in phase['groups'].items()}
    row['phase_profile']=compact
print(json.dumps(row))
assert not receipt.get('remaining_processes'),'owned process remains'
