"""Read the selected simulator config, including an object member pasted alone."""
import json

from check_sim_target import DEFAULT_CONFIG, check_fallback, config_path
from check_target_format import HOST, valid_deployment


def main():
    try:
        raw = config_path(DEFAULT_CONFIG).read_text(encoding='utf-8-sig')
        if len(raw) > 65536:
            return {'status': 'unexpected_config_size'}
        repaired = False
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as error:
            if error.msg != 'Extra data' or raw[error.pos:error.pos + 1] != ':':
                raise
            candidate = json.loads('{' + raw + '}')
            if not valid_deployment(candidate):
                return {'status': 'invalid_json', 'reason': 'unverified_object_member'}
            data, repaired = candidate, True
        result = check_fallback(data, HOST)
        if repaired:
            result['config_parsed_as_object_member'] = True
        return result
    except json.JSONDecodeError as error:
        return {'status': 'invalid_json', 'line': error.lineno,
                'column': error.colno, 'parse_error': error.msg}
    except (OSError, UnicodeError) as error:
        return {'status': 'config_read_failed', 'error_type': type(error).__name__}


if __name__ == '__main__':
    print(json.dumps(main()))
