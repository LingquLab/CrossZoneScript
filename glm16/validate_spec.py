"""Parse the staged serve command with the installed vLLM CLI, without serving."""
import json
from pathlib import Path

from vllm.entrypoints.cli.serve import ServeSubcommand
from vllm.utils.argparse_utils import FlexibleArgumentParser

spec = json.loads(Path('/experiment/launch-spec.json').read_text())
parser = FlexibleArgumentParser()
subparsers = parser.add_subparsers(dest='subcommand')
ServeSubcommand().subparser_init(subparsers)
args = parser.parse_args(spec['argv'][1:])
print('GLM_CLI_VALIDATED=' + json.dumps({'node_rank': spec['node_rank'],
    'tensor_parallel_size': args.tensor_parallel_size, 'nnodes': args.nnodes,
    'headless': bool(getattr(args, 'headless', False))}), flush=True)
