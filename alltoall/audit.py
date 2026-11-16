"""Return bounded final audit of the authorized AllToAll regression.
回传本任务最终核验结果，不运行新用例。
"""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import arguments,in_container,ROOT,REV
arguments()
s=Path(__file__).with_name('audit_remote.py').read_text().replace('ROOT_VALUE',repr(ROOT)).replace('REV_VALUE',repr(REV))
try:in_container(s,180)
except Exception as exc:print(json.dumps(dict(status='audit_failed',error_type=type(exc).__name__,error=str(exc)[:300])))
