"""Exercise the complete offline exporter against 20 synthetic accepted captures."""
from pathlib import Path
from unittest.mock import patch
import ast,contextlib,copy,hashlib,io,json,sys,tempfile,types,unittest
import export_remote as export


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2)+'\n')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(rank,number):
    path=Path('/users/fbh/workspace/TileXR-tilelang-2rank-perf-107-20261117/examples/tilelang_dispatch/analyze_a5.py')
    constants={}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name) and node.targets[0].id in ('FUNCTIONS','MODULES'):
            constants[node.targets[0].id]=ast.literal_eval(node.value)
    lanes=[]
    for core in range(64):
        intervals={};tick=100
        for function,names in constants['FUNCTIONS'].items():
            for name in names:
                if (function=='F8' or name in ('F7.receive_plan','F9.payload_clear')) and core>=4:continue
                if function in ('F2','F3','F5','F6') and core!=63:continue
                if function=='F4' and core==63:continue
                intervals[name]=dict(begin_tick=tick,end_tick=tick+10+core,ticks=10+core);tick+=10+core
        intervals['module.whole']=dict(begin_tick=100,end_tick=tick+10,ticks=tick-90)
        workload=dict.fromkeys(export.WORK,0);workload.update(received_records=128 if core<4 else 0,received_wire_bytes=131072 if core<4 else 0)
        lanes.append(dict(core=core,intervals=intervals,workload=workload))
    def group(names):
        return dict(per_core_sum_ticks={lane['core']:sum(lane['intervals'][name]['ticks'] for name in names if name in lane['intervals']) for lane in lanes if any(name in lane['intervals'] for name in names)})
    return dict(capture_id='fixture-%d-%d'%(rank,number),operator_correctness='PASS',capture_structure='PASS',performance_sample_accepted=True,lanes=lanes,
                functions={name:group(names) for name,names in constants['FUNCTIONS'].items()},
                modules={name:group([s for f in fs for s in constants['FUNCTIONS'][f]]) for name,fs in constants['MODULES'].items()},
                stages={name:group([name]) for name in {n for lane in lanes for n in lane['intervals']}})


class Export(unittest.TestCase):
    def test_twenty_capture_export_manifest_and_null_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);groups=[];analyses={}
            for relative in ('source','generated','fixture','install','build','core-install','correctness','correctness-launch','correctness-entry01','capture','capture-launch','capture-entry01','deps/backend/src/tl_templates'):
                (root/relative).mkdir(parents=True,exist_ok=True)
            save(root/'build-summary.json',dict(candidate='fixture-candidate',experiment_revision=export.REVISION))
            save(root/'correctness-launch/summary.json',dict(status='PASS'))
            for number in range(1,11):
                index=number+2;directory=root/'capture'/('g%03d'%index);ranks=[];pairs=[]
                for rank in (0,1):
                    cap=capture(rank,number);ranks.append(dict(rank=rank,cores=64,captures=[cap]))
                    pairs.append(dict(rank=rank,acl_event_us=100+number,device_task_us=90+rank,pre_allreduce=dict(sum_results_checked=22,pre_dispatch_event_us=200+rank,segment_event_us=300+number+rank)))
                    path=directory/'native/round1'/('rank'+str(rank))/'round20.dispatch.records';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(cap['capture_id'].encode())
                analysis=dict(status='PASS',rounds=1,warmup=20,rank_results=ranks,pairs=[dict(ranks=pairs)])
                save(directory/'analysis.json',analysis);save(directory/'summary.json',dict(status='PASS'));save(directory/'capture-evidence.json',{})
                groups.append(dict(index=index,status='PASS',summary_sha256=digest(directory/'summary.json'),analysis_sha256=digest(directory/'analysis.json'),capture_evidence_sha256=digest(directory/'capture-evidence.json')))
                analyses[str(directory/'native')]=analysis
            save(root/'capture/summary.json',dict(status='PASS'))
            parent=dict(status='PASS',namespace_exit=0,timed_out=False,cleanup_actions=[],own_residual_children=[],original_configuration_preserved=True,groups=groups,worker_summary_sha256=digest(root/'capture/summary.json'))
            save(root/'capture-launch/summary.json',parent)
            analyzer=types.ModuleType('tilelang_dispatch.analyze_native');analyzer.analyze=lambda path,*args:copy.deepcopy(analyses[str(path)])
            package=types.ModuleType('tilelang_dispatch');package.__path__=[]
            with patch.object(export,'ROOT',root),patch.object(export,'OUT',root/'validated-export01'),patch.object(export,'PARENT_SHA',digest(root/'capture-launch/summary.json')),patch.object(export,'CORRECTNESS_SHA',digest(root/'correctness-launch/summary.json')),patch.object(export,'integrity'),patch.object(export,'source_check'),patch.dict(sys.modules,{'tilelang_dispatch':package,'tilelang_dispatch.analyze_native':analyzer}),contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(export.main(),0)
            result=json.loads(stdout.getvalue());self.assertEqual(result['sdk_record_parses'],20)
            self.assertTrue(result['archive']['crc_and_all_member_sha_verified']);self.assertLess(len(stdout.getvalue().encode()),15000)
            stats=json.loads((root/'validated-export01/all-statistics.json').read_text())
            self.assertEqual(stats['0']['functions.F8.non_receiver']['status'],'NOT_EXECUTED')
            self.assertIsNone(stats['0']['functions.F8.non_receiver']['mean_ticks'])
            self.assertEqual(stats['0']['modules.receive_output.receiver']['participants'],4)
            self.assertEqual(stats['0']['modules.receive_output.non_receiver']['participants'],60)
            self.assertEqual(len(result['workload_rows']),4);self.assertTrue(all(len(row)==13 for row in result['workload_rows']))


if __name__=='__main__':unittest.main()
