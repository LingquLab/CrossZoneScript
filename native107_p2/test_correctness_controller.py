"""[AI] CPU-only regressions for busy/unknown and first-failure execution guards."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import importlib.util, json, os, tempfile, unittest
from unittest.mock import patch
P=Path(__file__).with_name('runtime-correctness.py')
spec=importlib.util.spec_from_file_location('runtime',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Guards(unittest.TestCase):
    def test_disappeared_process_is_gone_other_errors_unknown(self):
        for error in (FileNotFoundError,ProcessLookupError):
            with patch.object(Path,'read_text',side_effect=error):self.assertIsNone(m.state(99999))
        with patch.object(Path,'read_text',side_effect=PermissionError):
            with self.assertRaises(PermissionError):m.state(99999)
    def test_reused_pid_not_owned(self):
        r={'pid':1,'start_ticks':'10'}
        with patch.object(m,'state',return_value={'pid':1,'start_ticks':'11','state':'R'}),patch.object(m.os,'kill') as kill:
            self.assertFalse(m.live(r));self.assertEqual(m.reap_owned({1:r}),[]);kill.assert_not_called()
    def test_occupancy_failures_stop(self):
        for code,report in ((1,{'safe_to_use':False}),(3,{'safe_to_use':False}),(0,{'safe_to_use':False})):
            with tempfile.TemporaryDirectory() as td,patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=code,stdout=json.dumps(report).encode(),stderr=b'')) as cmd:
                with self.assertRaisesRegex(RuntimeError,'BUSY_OR_UNKNOWN'):m.device_gate(Path(td),'x')
                self.assertEqual(cmd.call_count,1)
    def test_malformed_probe_is_unknown(self):
        with tempfile.TemporaryDirectory() as td,patch.object(m.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=b'not-json',stderr=b'')):
            with self.assertRaises(ValueError):m.device_gate(Path(td),'x')
    def test_wrong_core_count_stops(self):
        responses=[SimpleNamespace(returncode=0,stdout=b'',stderr=b''),SimpleNamespace(returncode=0,stdout=b'init 0\ncore 0 0 56\ncore 1 0 64\nfinal 0\n',stderr=b'')]
        with tempfile.TemporaryDirectory() as td,patch.object(m.subprocess,'run',side_effect=responses):
            with self.assertRaisesRegex(RuntimeError,'ACTUAL_CORE_COUNT'):m.core_query(Path(td))
    def test_first_group_gate_prevents_all_launches(self):
        fake=ModuleType('run_dispatch_pre_allreduce');fake.candidate_identity=lambda p:{}
        with tempfile.TemporaryDirectory() as td,patch.object(m,'RUN',Path(td)),patch.dict('sys.modules',{'run_dispatch_pre_allreduce':fake}),patch.object(m,'integrity',return_value={}),patch.object(m,'process_gate'),patch.object(m,'device_gate',side_effect=RuntimeError('BUSY_OR_UNKNOWN')),patch.object(m,'launch') as launch,patch.dict(os.environ,{'TILEXR_UDMA_ATTACH_EXISTING_RA':'1'}):
            self.assertEqual(m.worker(),1);launch.assert_not_called();v=json.loads((Path(td)/'worker-summary.json').read_text());self.assertEqual(len(v['groups']),1);self.assertFalse(v['npu_launch']);self.assertEqual(v['status'],'FAIL_OR_UNVERIFIED')
    def test_fresh_run_not_replayed(self):
        with tempfile.TemporaryDirectory() as td,patch.object(m,'RUN',Path(td)),patch.object(m,'integrity'):
            with self.assertRaisesRegex(RuntimeError,'SAVED_RUN_EXISTS_NO_REPLAY'):m.parent()
if __name__=='__main__':unittest.main()
