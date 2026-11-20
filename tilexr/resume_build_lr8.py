"""Continue after the confirmed Docker CLI failure; retain attempt-one evidence."""
import json

import build_lr8


compatibility = json.loads((build_lr8.REPORT / 'docker-compatibility.json').read_text(encoding='utf-8'))
assert compatibility['container_exists'] is False
assert compatibility['work_directory_entries'] == []
assert compatibility['supports_pull_option'] is False
build_lr8.REPORT = build_lr8.ROOT / 'reports/lr8-2'
build_lr8.main()
