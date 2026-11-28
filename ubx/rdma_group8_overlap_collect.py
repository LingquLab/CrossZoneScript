import runpy
from pathlib import Path
for entry in ['rdma_group8_overlap_stats.py','rdma_group8_overlap_export.py']:
 runpy.run_path(str(Path(__file__).with_name(entry)),run_name='__main__')
