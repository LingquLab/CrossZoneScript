"""Measure synthetic completion requests by returned token IDs, including empty text."""
import argparse
import json
from pathlib import Path
import random
import statistics
import time
import urllib.request

def percentile(values, percent):
    values = sorted(values)
    position = (len(values) - 1) * percent / 100
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)

def measure(endpoint, seed):
    rng = random.Random(seed)
    prompt = [1000 + seed] + [rng.randrange(100, 50000) for _ in range(8191)]
    body = {'model': 'glm-5.2', 'prompt': prompt, 'max_tokens': 128,
            'temperature': 0, 'seed': seed, 'ignore_eos': True, 'stream': True,
            'return_token_ids': True, 'stream_options': {'include_usage': True}}
    request = urllib.request.Request(endpoint.rstrip('/') + '/v1/completions',
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    timestamps = []
    usage = None
    stream_done = False
    started = time.perf_counter()
    with opener.open(request, timeout=180) as response:
        assert response.status == 200
        for raw in response:
            arrived = time.perf_counter()
            line = raw.decode('utf-8').strip()
            if not line.startswith('data:'):
                continue
            payload = line[5:].strip()
            if payload == '[DONE]':
                stream_done = True
                break
            event = json.loads(payload)
            assert 'error' not in event, 'server_stream_error'
            if event.get('usage') is not None:
                usage = event['usage']
            for choice in event.get('choices', []):
                ids = choice.get('token_ids') or []
                if ids:
                    assert choice.get('index', 0) == 0
                    assert len(ids) == 1, 'cannot_report_individual_ITL_for_batched_token_events'
                    timestamps.append(arrived)
    finished = time.perf_counter()
    assert stream_done and len(timestamps) == 128, 'output_token_count_mismatch'
    assert usage and usage['prompt_tokens'] == 8192 and usage['completion_tokens'] == 128
    intervals = [(b - a) * 1000 for a, b in zip(timestamps, timestamps[1:])]
    return {'seed': seed, 'input_tokens': 8192, 'output_tokens': 128,
            'token_events': len(timestamps), 'ttft_ms': (timestamps[0] - started) * 1000,
            'tpot_ms': (timestamps[-1] - timestamps[0]) * 1000 / 127,
            'e2e_s': timestamps[-1] - started, 'stream_complete_s': finished - started,
            'itl_ms': intervals}

def benchmark(endpoint, output):
    output.mkdir(parents=True, exist_ok=True)
    warmup = measure(endpoint, 1600)
    requests = [measure(endpoint, seed) for seed in range(1601, 1605)]
    intervals = [value for row in requests for value in row['itl_ms']]
    summary = {'input_tokens': 8192, 'output_tokens': 128, 'concurrency': 1,
               'warmup_requests': 1, 'measured_requests': len(requests),
               'mean_ttft_ms': statistics.mean(x['ttft_ms'] for x in requests),
               'mean_tpot_ms': statistics.mean(x['tpot_ms'] for x in requests),
               'mean_e2e_s': statistics.mean(x['e2e_s'] for x in requests),
               'itl_p50_ms': percentile(intervals, 50),
               'itl_p95_ms': percentile(intervals, 95),
               'itl_p99_ms': percentile(intervals, 99)}
    (output / 'requests.json').write_text(json.dumps({'warmup': warmup, 'requests': requests}, indent=2))
    (output / 'performance.json').write_text(json.dumps(summary, indent=2))
    return summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint', default='http://173.27.3.58:8016')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.endpoint, args.output)))
