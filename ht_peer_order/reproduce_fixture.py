"""Recreate a paired benchmark fixture offline from an unchanged TileXR checkout."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from fixture import BASE, generate_inputs
from phases import MODES, REVISION, STEM, write_fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tilexr', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--tokens', type=int, choices=(32, 64), default=32)
    parser.add_argument('--seed', type=int, default=BASE)
    args = parser.parse_args()

    checkout = args.tilexr.resolve()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=checkout, text=True).strip()
    if revision != REVISION:
        parser.error(f'TileXR checkout must be at {REVISION}')
    sys.path.insert(0, str(checkout / 'tools/unitest/simulator/experiments/inter_server_send/sst'))
    import loader
    import native
    import runtime

    directory = Path(__file__).resolve().parent
    kernel = gzip.decompress((directory / 'kernel.o.gz').read_bytes())
    digest = (directory / 'kernel.sha256').read_text().strip()
    if hashlib.sha256(kernel).hexdigest() != digest:
        raise ValueError('The benchmark object does not match the recorded hash')
    args.output.mkdir(parents=True, exist_ok=False)
    binary = args.output / 'kernel.o'
    binary.write_bytes(kernel)
    abis = json.loads(gzip.decompress((directory / 'abis.json.gz').read_bytes()))[str(args.tokens)]
    data = generate_inputs(args.tokens, args.seed, abis, (native, runtime))
    for mode in MODES:
        case = args.output / f'{STEM}t{args.tokens}-{args.seed}-{mode}'
        write_fixture(case, args.output / (case.name + '-golden'), case, mode, args.tokens,
                      args.seed, data, abis, binary, digest, (loader, native, runtime))


if __name__ == '__main__':
    main()
