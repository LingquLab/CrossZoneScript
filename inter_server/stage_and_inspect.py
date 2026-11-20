"""Stage the native blue object and inspect the SST model; no build or SST launch."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
sys.path.insert(0,str(Path(__file__).parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'alltoall'))
from ssh_transport import run

buffer=io.StringIO()
with contextlib.redirect_stdout(buffer):
    staged=runpy.run_path(str(Path(__file__).with_name('stage_binary.py')))
result={'binary_stage':staged['result'],'model_inspection':run(Path(__file__).with_name('inspect_model_remote.py').read_text(encoding='utf-8'),timeout=100)}
report=Path('D:/project/inter-server-sst-reports/b4f4660a-6144-453c-99f7-339b40f56c54')
(report/'native-model-inspection.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=True))
