import argparse
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
args = json.loads(Path(parser.parse_args().args_file).read_text(encoding='utf-8'))
entry = Path(__file__).with_name('model_benchmark.py')
results = []
with tempfile.TemporaryDirectory() as directory:
    for model in args['models']:
        p = Path(directory)/'args.json'
        p.write_text(json.dumps({'mode':'review','model':model}), encoding='utf-8')
        sys.argv = [str(entry),'--args-file',str(p)]
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output):
                runpy.run_path(str(entry), run_name='__main__')
            records = [json.loads(line) for line in output.getvalue().splitlines()]
            result = {'model':model, **records[-1]}
        except Exception as exc:
            result = {'model':model,'error_type':type(exc).__name__}
        results.append(result)
        Path('model-benchmark-results.json').write_text(json.dumps(results), encoding='utf-8')
        print(json.dumps(result), flush=True)
