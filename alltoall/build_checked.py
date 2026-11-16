"""Return bounded preparation failures before build deployment.
构建部署前的错误仅回传精简异常信息。
"""
from pathlib import Path
import runpy
import sys
import traceback
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    runpy.run_path(str(Path(__file__).with_name('build.py')), run_name='__main__')
except Exception as exc:
    import json
    frames=traceback.extract_tb(exc.__traceback__)
    print(json.dumps(dict(status='preparation_failed',error_type=type(exc).__name__,
        error=str(exc)[:500],frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name) for f in frames[-5:]])))
