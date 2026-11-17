"""CPU checks of ownership, explicit ports and first-error/no-capture gating."""
from pathlib import Path
from types import SimpleNamespace
import json,os,subprocess,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,'/users/fbh/workspace/TileXR-tilelang-2rank-perf-107-20261117/examples')
import controller,harness,phase_parent as parent

class Ownership(unittest.TestCase):
    def test_terminal_exit_and_timeout_own_only(self):
        for code in (0,7):
            with tempfile.TemporaryDirectory() as tmp:
                p=subprocess.Popen([sys.executable,'-c','import time,sys;time.sleep(.15);sys.exit('+str(code)+')'],start_new_session=True)
                r=parent.monitor(p,Path(tmp),3)
                self.assertEqual(r['namespace_exit'],code);self.assertFalse(r['timed_out']);self.assertEqual(r['cleanup_actions'],[]);self.assertEqual(r['own_residual_children'],[])
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                code='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],start_new_session=True);time.sleep(30)'
                p=subprocess.Popen([sys.executable,'-c',code],start_new_session=True)
                r=parent.monitor(p,Path(tmp),.5)
                self.assertTrue(r['timed_out']);self.assertGreaterEqual(len(r['cleanup_actions']),2);self.assertEqual(r['own_residual_children'],[]);self.assertIsNone(other.poll())
        finally:other.terminate();other.wait(timeout=5)
    def test_disappearance_and_pid_reuse(self):
        for e in (FileNotFoundError(),ProcessLookupError()):
            with patch.object(Path,'read_text',side_effect=e):self.assertIsNone(parent.identity(os.getpid()))
        with patch.object(parent,'identity',return_value={'pid':42,'start_ticks':9,'state':'S'}):self.assertFalse(parent.live({'pid':42,'start_ticks':8}))

class Harness(unittest.TestCase):
    def test_explicit_ports_and_failed_rank_cleanup(self):
        for fail in (False,True):
            with self.subTest(fail=fail),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);bins=[]
                for rank in (0,1):
                    p=root/f'binary{rank}'
                    p.write_text('#!'+sys.executable+'\nimport os,sys,json,time\nfrom pathlib import Path\n'+
                        "assert sys.argv[1]=='--native' and sys.argv[5]=='20' and sys.argv[6]=='30000' and sys.argv[7]=='allreduce'\n"+
                        "assert os.environ['TILEXR_COMM_ID']=='127.0.0.1:30001'\n"+
                        ('sys.exit(3)\n' if fail and rank==0 else 'time.sleep(.3)\n'))
                    p.chmod(0o750);bins.append(p)
                args=SimpleNamespace(devices=[0,1],fixture=root/'fixture',warmup=20,barrier_port=30000,comm_port=30001,pre_allreduce='allreduce',timeout=5,cores=[64,64])
                with patch.object(harness.native,'check_idle'):
                    if fail:
                        with self.assertRaises(RuntimeError):harness.run_round(args,root/'out',bins,None)
                    else:self.assertEqual(harness.run_round(args,root/'out',bins,None)['status'],'PASS')
                exits=json.loads((root/'out/process-exits.json').read_text());self.assertEqual(len(exits),2);self.assertTrue(all(not x['group_remaining'] for x in exits))

class Ordering(unittest.TestCase):
    def test_first_error_stops_and_capture_requires_correctness(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(controller,'ROOT',root),patch.object(controller,'build_integrity',return_value={}),patch.object(sys,'argv',['controller.py','correctness']),patch.object(controller,'one_group',side_effect=RuntimeError('first-error')) as group:
                self.assertEqual(controller.main(),1);self.assertEqual(group.call_count,1)
            with patch.object(controller,'ROOT',root),patch.object(controller,'build_integrity',return_value={}),patch.object(sys,'argv',['controller.py','capture']),patch.object(controller,'one_group') as group:
                self.assertEqual(controller.main(),1);group.assert_not_called()
            self.assertEqual(json.loads((root/'capture/summary.json').read_text())['npu_launch'],False)
if __name__=='__main__':unittest.main()
