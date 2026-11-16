"""[AI] Verify same-core sums, cohort statistics and archive integrity with CPU fixtures."""
from pathlib import Path
import importlib.util,tempfile,unittest,zipfile
from unittest.mock import patch
s=importlib.util.spec_from_file_location('export',Path(__file__).with_name('export-numeric.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
def core(i,spans):return {'core':i,'functional_events':{n:{'active':True,'begin_tick':a,'end_tick':b} for n,(a,b) in spans.items()}}
class Numeric(unittest.TestCase):
 def test_same_core_disjoint_sum_and_missing_is_not_zero(self):
  c=core(0,{'F7':(10,20),'F8':(30,40),'F9':(40,60)})
  self.assertEqual(m.ticks(c,['F7','F8','F9']),40)
  self.assertIsNone(m.ticks(c,['absent']))
  c['functional_events']['F8']['begin_tick']=15
  with self.assertRaisesRegex(RuntimeError,'OVERLAPPING_SUM'):m.ticks(c,['F7','F8'])
 def test_cohort_mean_max_uses_real_same_core_values(self):
  group=[core(0,{'A':(1,101),'B':(200,201)}),core(1,{'A':(1,2),'B':(200,300)})]
  row=m.stage_row(1,0,'receive','M5',group,['A','B']);self.assertEqual(row['mean_ticks'],101);self.assertEqual(row['max_ticks'],101);self.assertEqual(row['slowest_cores'],[0,1])
 def test_aggregate_equal_rank_round_weight_and_true_max_identity(self):
  rows=[dict(round=r,rank=k,cohort='receive',stage='M5',status='MEASURED',mean_ticks=10 if k==0 else 30,max_ticks=100 if (r,k) in ((1,1),(9,0)) else 40,slowest_cores=[k],mask='0-3',participating_cores=4) for r in range(1,11) for k in (0,1)]
  a=m.aggregate(rows)[0];self.assertEqual(a['mean_ticks'],20);self.assertEqual(a['max_ticks'],100);self.assertEqual(a['slowest'],[{'round':1,'rank':1,'cores':[1]},{'round':9,'rank':0,'cores':[0]}])
 def test_zip_crc_and_members(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);source=p/'raw';source.mkdir();(source/'records.txt').write_text('measured evidence\n')
   with patch.object(m,'OUT',p):v=m.archive([('capture',source)],p/'raw.zip')
   self.assertEqual(v['crc'],'PASS');self.assertEqual(v['members'],2)
   with zipfile.ZipFile(p/'raw.zip') as z:self.assertEqual(z.read('capture/records.txt'),b'measured evidence\n')
 def test_archive_missing_source_stops_instead_of_omitting_evidence(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)
   with patch.object(m,'OUT',p),self.assertRaisesRegex(RuntimeError,'ARCHIVE_SOURCE_MISSING'):
    m.archive([('capture',p/'missing')],p/'raw.zip')
   self.assertFalse((p/'raw.zip').exists())
if __name__=='__main__':unittest.main()
