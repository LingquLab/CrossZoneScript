"""[AI] CPU regressions for capture gating, first-failure stopping and profiler evidence."""
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import importlib.util,json,os,tempfile,unittest
P=Path(__file__).with_name('runtime-capture.py');s=importlib.util.spec_from_file_location('capture',P);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class Capture(unittest.TestCase):
 def test_prior_receipt_changed_blocks(self):
  with patch.object(m,'sha',return_value='changed'):
   with self.assertRaisesRegex(RuntimeError,'CORRECTNESS_SUMMARY_SHA'):m.correctness_gate(None,{})
 def test_collect_disappeared_child_race(self):
  with patch.object(m.b,'state',return_value={'pid':12,'start_ticks':'5'}),patch.object(Path,'read_text',side_effect=ProcessLookupError):
   r={};m.collect(12,r);self.assertIn(12,r)
  with patch.object(m.b,'state',return_value={'pid':12,'start_ticks':'6'}):
   with self.assertRaisesRegex(RuntimeError,'OWN_PID_REUSED'):m.collect(12,r)
 def test_permission_error_stops_monitor(self):
  with patch.object(m.b,'state',return_value={'pid':12,'start_ticks':'5'}),patch.object(Path,'read_text',side_effect=PermissionError):
   with self.assertRaises(PermissionError):m.collect(12,{})
 def test_report_rejects_missing_lane_or_wrong_generation(self):
  ranks=[{'rank':r,'block_dim':64,'cores':[{'core':i,'functional_events':{}} for i in range(64)],'modules':dict.fromkeys(['M1','M2','M3','M4','M5']),'function_statistics':dict.fromkeys('F'+str(i) for i in range(10)),'profiler_match':{'dispatch_tasks':21,'generation':21},'device_task_us':100.} for r in (0,1)]
  m.validate_report({'ranks':ranks});ranks[1]['cores'].pop()
  with self.assertRaisesRegex(RuntimeError,'CAPTURE_CORE_COVERAGE'):m.validate_report({'ranks':ranks})
  ranks[1]['cores'].append({'core':63,'functional_events':{}});ranks[0]['profiler_match']['generation']=20
  with self.assertRaisesRegex(RuntimeError,'PROFILE_GENERATION'):m.validate_report({'ranks':ranks})
 def worker_case(self,fail_round=None,busy=False):
  fake=ModuleType('run_dispatch_pre_allreduce');fake.candidate_identity=lambda p:{};fake.validate_round=lambda *a:[]
  report={'ranks':[{'rank':r,'device_task_us':100.} for r in (0,1)]}
  fake.base=SimpleNamespace(capture_report=lambda *a:report);fake.enrich_capture=lambda *a,**k:report
  count=[];ports=[]
  def launch(a,d,*unused):
   count.append(d.name)
   if len(count)==fail_round:raise RuntimeError('TEST_FIRST_ERROR')
  def gate(*a):
   if busy:raise RuntimeError('BUSY_OR_UNKNOWN')
   return {'occupancy':'IDLE'}
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);(root/'msprof').write_text('fake')
   with patch.object(m.b,'RUN',root),patch.object(m.b,'CANN',root),patch.object(m.b,'integrity',return_value={}),patch.object(m.b,'process_gate'),patch.object(m.b,'device_gate',side_effect=gate),patch.object(m.b,'ports',side_effect=lambda i:ports.append(i) or (1,2,3)),patch.object(m,'correctness_gate',return_value={}),patch.object(m.shutil,'which',return_value=str(root/'msprof')),patch.object(m,'launch',side_effect=launch),patch.object(m,'validate_lifecycle'),patch.object(m,'validate_report'),patch.dict('sys.modules',{'run_dispatch_pre_allreduce':fake}),patch.dict(os.environ,{'TILEXR_UDMA_ATTACH_EXISTING_RA':'1','HCCL_IF_BASE_PORT':'25000'}):
    code=m.worker();value=json.loads((root/'worker-summary.json').read_text())
  return code,value,count,ports
 def test_busy_gate_never_launches(self):
  code,v,c,p=self.worker_case(busy=True);self.assertEqual(code,1);self.assertEqual(c,[]);self.assertEqual(len(v['groups']),1)
 def test_first_error_prevents_following_groups(self):
  code,v,c,p=self.worker_case(fail_round=2);self.assertEqual(code,1);self.assertEqual(c,['round-1','round-2']);self.assertEqual(p,[4,5]);self.assertEqual(len(v['groups']),2)
 def test_ten_fresh_capture_groups_no_correctness_launch(self):
  code,v,c,p=self.worker_case();self.assertEqual(code,0);self.assertEqual(v['capture_groups_passed'],10);self.assertEqual(p,list(range(4,14)));self.assertEqual(len(c),10)
if __name__=='__main__':unittest.main()
