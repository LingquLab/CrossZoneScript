"""Diagnose target JSON without returning any file contents or login values."""
import ast
import json
import re
from pathlib import Path

from check_sim_target import DEFAULT_CONFIG, config_path


HOST = '141.61.8.185'


def read_candidates(raw, error):
    if raw.startswith('```') and raw.rstrip().endswith('```'):
        yield 'markdown_fence', raw.partition('\n')[2].rstrip()[:-3]
    if re.search(r',\s*[}\]]', raw):
        yield 'trailing_comma', re.sub(r'"(?:\\.|[^"\\])*"|,(?=\s*[}\]])',
                                       lambda match: '' if match[0] == ',' else match[0], raw)
    pos = error.pos
    if error.msg == "Expecting ':' delimiter" and raw[pos:pos + 1] == '=':
        yield 'key_delimiter', raw[:pos] + ':' + raw[pos + 1:]
    if error.msg == "Expecting ':' delimiter" and raw[pos:pos + 1] in '{["':
        yield 'missing_key_delimiter', raw[:pos] + ':' + raw[pos:]
    if error.msg == 'Expecting property name enclosed in double quotes':
        bare_host = re.compile(r'(?<=[{,])(\s*)(141\.61\.8\.185)(?=\s*:)')
        if bare_host.search(raw):
            yield 'unquoted_host_key', bare_host.sub(r'\1"\2"', raw, count=1)
    try:
        result = ast.literal_eval(raw)
        if isinstance(result, dict):
            yield 'python_mapping', json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError, SyntaxError, RecursionError):
        pass


def valid_deployment(data):
    if not isinstance(data, dict):
        return False
    item = data.get(HOST)
    server = item.get('server') if isinstance(item, dict) else None
    deploy = item.get('deployment') if isinstance(item, dict) else None
    return (
        isinstance(server, dict) and server.get('host') == HOST
        and server.get('auth') == 'password' and isinstance(deploy, dict)
        and deploy.get('image') == 'tilexr-sim-9.1:aarch64-v1.1.2'
    )


def main():
    path = config_path(DEFAULT_CONFIG)
    try:
        raw = path.read_text(encoding='utf-8-sig')
        if len(raw) > 65536:
            return {'status': 'unexpected_config_size'}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            safe_status = {'status': 'invalid_json', 'line': error.lineno,
                           'column': error.colno, 'parse_error': error.msg}
            if error.pos < len(raw):
                safe_status['found_structural_token'] = {
                    ',' : 'comma', ':' : 'colon', '=' : 'equals', '{' : 'brace',
                    '}' : 'closing_brace', '"' : 'quote', '\\' : 'backslash',
                    '“': 'smart_quote', '”': 'smart_quote',
                }.get(raw[error.pos], 'other')
            for kind, candidate in read_candidates(raw, error):
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if valid_deployment(data):
                    server = data[HOST]['server']
                    return dict(safe_status, repair=kind,
                                login_value_present=bool(server.get('password')),
                                pinned_identity_present=bool(server.get('host_key_sha256')))
            return safe_status
        return {'status': 'parseable', 'deployment_matches': valid_deployment(data)}
    except (OSError, UnicodeError) as error:
        return {'status': 'config_read_failed', 'error_type': type(error).__name__}


if __name__ == '__main__':
    print(json.dumps(main()))
