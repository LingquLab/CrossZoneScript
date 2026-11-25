"""Launch the staged and recorded vLLM command inside the owned test container."""
import json
import os
from pathlib import Path

spec = json.loads(Path('/experiment/launch-spec.json').read_text())
os.environ.update(spec['environment'])
os.execvp('vllm', spec['argv'])
