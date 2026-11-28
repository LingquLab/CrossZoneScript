"""Compile the pushed repository SST entries in the pinned CANN image."""
import argparse
from pathlib import Path
import json
import os

import bootstrap_green
from green import execute
from transport import connect

REVISION = '4d37ae67cf2f6baa06b3efbe65c71649a6f24203'
parser = argparse.ArgumentParser()
parser.add_argument('--args-file', required=True)
parser.parse_args()
log = Path(os.environ['LOCALAPPDATA']) / 'CrossZone/tilexr' / bootstrap_green.SESSION / ('delivery-' + REVISION[:7])
log.mkdir(parents=True, exist_ok=True)
try:
    source = bootstrap_green.source_request('delivery', REVISION, log)
    with connect('18', 'password') as client:
        code, result = execute(client, 'check_delivery_host.py', source, 400, log)
    print(json.dumps(result))
    raise SystemExit(code)
except Exception as err:
    (log/'error-class.txt').write_text(type(err).__name__)
    raise SystemExit(1)
