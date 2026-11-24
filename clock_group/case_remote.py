"""运行精确时钟/对照对象的单个64P用例；保留严格结果和仅用于诊断的迟到观测。"""
import json
from pathlib import Path
import sys

root = Path(sys.argv[1]); spec = json.loads(sys.argv[2]); source = root/'source'
sys.path.insert(0, str(source/'tools/unitest'))
from controller import memory_sst as mem, direct_group_clock as clock
from controller.case import load_case

build = json.loads((root/('build-'+spec['variant']+'.json')).read_text())
revision = build['runtime_commit']; label = spec['label']
output = root/'cases'/label
suite = Path('/opt/sim/simulator/Testcases')/('clock_'+revision[:8]+'_'+label)
assert not output.exists() and not suite.exists(), 'preserve prior case evidence'
output.mkdir(parents=True)
total = {'small': 64*512, '512k': 524288, '4m': 4194304}[spec['size']]
raw = json.loads((source/'tools/unitest/cases/alltoall-mem-two-group.json').read_text())
raw.update(world_size=64, elements_per_peer=total//64//4, id='clock-'+label)
case_path = output/'case.json';case_path.write_text(json.dumps(raw, indent=2))
case = load_case(case_path, list(range(64)))
config = dict(schema='tilexr.unitest.sst.memory.v1', rank_size=64, suite_output=str(suite),
    build_report=str(root/('build-'+spec['variant']+'.json')), environment_report=str(root/'environment-report.json'),
    threads=64, simulation_timeout=900, iterations=1)
if spec['variant'] == 'clock':
    config['clock'] = clock.identity_schedule(spec['target'], max(20000, spec['target']*4), spec['frequency'])
(output/'config.json').write_text(json.dumps(config, indent=2))
config = mem.load_config(output/'config.json', config)
report = mem.run(config, case, dict(mode='single', profiling=dict(enabled=False)), {}, output)
receipt = json.loads((suite/'process_receipt.json').read_text()) if (suite/'process_receipt.json').exists() else {}
phase = report.get('clock_phase')
diagnostic_error = None
if phase is None and 'clock' in config and receipt.get('returncode') == 0:
    try:
        import yaml
        chips = yaml.safe_load((suite/'v310_config/workload_config.yml').read_text())['workload_config']
        ranks=[];payload_ranks=0
        for rank in range(64):
            prefix=mem.guarded_output(case, rank)
            initial=prefix+clock.initial(config['clock'], rank)
            chip=chips['chip%d'%rank]; dump=chip['output_param']['output']
            actual=mem.merge_die_dumps([suite/'bin'/('david_%d_die%d_output.bin'%(rank,die)) for die in (1,2)],
                dump['addr']-chip['sys_va_base_addr'], initial)
            payload_ranks+=actual[:len(prefix)]==mem.guarded_output(case,rank,True)
            ranks.append(dict(clock_records=clock.decode(actual[len(prefix):],config['clock'],rank,require_on_time=False)))
        phase=clock.summarize(ranks,config['clock'],require_on_time=False)
        phase.update(diagnostic_only=True,diagnostic_payload_validated_ranks=payload_ranks)
    except (ValueError,OSError,KeyError) as exc:
        diagnostic_error=str(exc)[:200]
ranks=report.get('operator_result',{}).get('ranks',[])
simulation=report.get('phases',{}).get('simulation',{})
row=dict(runtime_commit=revision,kernel_sha256=build['kernel']['sha256'],variant=spec['variant'],size=spec['size'],
    bytes_per_peer=total//64,total_bytes_per_rank=total,block_dim=64,
    validated=report['status']=='passed' and len(ranks)==64,checked_ranks=len(ranks),
    simulated_us=simulation.get('simulated_us'),clock_phase=phase,diagnostic_error=diagnostic_error,
    error=report.get('error'),failed_phase=report.get('failed_phase'),returncode=receipt.get('returncode'),
    remaining_processes=receipt.get('remaining_processes'),report=str(output/'result.json'))
(output/'summary.json').write_text(json.dumps(row,indent=2));print(json.dumps(row))
assert not receipt.get('remaining_processes'),'owned processes remain'
