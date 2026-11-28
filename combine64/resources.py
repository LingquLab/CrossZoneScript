"""Read the selected SST host's available CPU and memory capacity."""
import argparse
import json
from pathlib import Path

from transport import connect, remote

parser = argparse.ArgumentParser()
parser.add_argument('--args-file', type=Path, required=True)
args = json.loads(parser.parse_args().args_file.read_text(encoding='utf-8-sig'))
assert args['host'] == '18'
with connect(args['host'], args['auth']) as client:
    command = ("test \"$(hostname -s)\" = host-94-18 && nproc && "
               "awk '/^MemAvailable:/ {print $2}' /proc/meminfo && "
               "cut -d ' ' -f 1,2,3 /proc/loadavg")
    code, output, error = remote(client, command, timeout=20)
    assert code == 0 and not error
    cpus, memory, load = output.decode('ascii').splitlines()
    print(json.dumps({'logical_cpus': int(cpus), 'available_gib': int(memory) // 1024**2,
                      'load_average': [float(value) for value in load.split()]}))
