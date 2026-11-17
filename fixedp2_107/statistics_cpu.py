"""Aggregate saved fixed-P2 capture lanes without mixing core denominators."""
import math
import statistics


def require(condition, message):
    if not condition:
        raise ValueError(message)


def distribution(values):
    require(values and all(math.isfinite(v) for v in values), 'Invalid distribution')
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                min=min(values), max=max(values))


def select(by_core, cohort):
    present = {int(c): ticks for c, ticks in by_core.items() if int(c) in cohort}
    if not present:
        return dict(status='NOT_EXECUTED', participants=0, mean_ticks=None,
                    max_ticks=None, slowest_cores=[])
    require(all(isinstance(v, int) and v >= 0 for v in present.values()), 'Invalid raw ticks')
    maximum = max(present.values())
    return dict(status='EXECUTED', participants=len(present),
                mean_ticks=statistics.mean(present.values()), max_ticks=maximum,
                slowest_cores=sorted(c for c, ticks in present.items() if ticks == maximum))


def capture_statistics(cap):
    lanes = cap['lanes']
    require([lane['core'] for lane in lanes] == list(range(64)), 'Expected actual 64-core capture')
    require([lane['core'] for lane in lanes if lane['workload']['received_records']] == list(range(4)),
            'Actual receiver mask differs from 0xf')
    require(all(lane['workload']['received_records'] == 128 for lane in lanes[:4]), 'Receiver workload changed')
    metrics = {}
    cohorts = {'all': set(range(64)), 'receiver': set(range(4)), 'non_receiver': set(range(4, 64))}
    for category in ('modules', 'functions', 'stages'):
        for name, group in cap[category].items():
            by_core = {int(c): ticks for c, ticks in group['per_core_sum_ticks'].items()}
            for cohort, cores in cohorts.items():
                metrics[category + '.' + name + '.' + cohort] = select(by_core, cores)
    # Independently recompose the five-module/F totals on each actual core.
    for core, lane in enumerate(lanes):
        functions = [group['per_core_sum_ticks'].get(core, group['per_core_sum_ticks'].get(str(core)))
                     for group in cap['functions'].values()]
        modules = [group['per_core_sum_ticks'].get(core, group['per_core_sum_ticks'].get(str(core)))
                   for group in cap['modules'].values()]
        require(sum(v for v in functions if v is not None) == sum(v for v in modules if v is not None),
                'Functions/modules do not compose on the same core')
    return metrics


def aggregate(rows):
    """rows contain (round, rank, per-capture-statistic), retaining tied identities."""
    require(rows, 'No rounds')
    counts = {item['participants'] for _, _, item in rows}
    require(len(counts) == 1, 'Participant denominator changes between rounds')
    participants = counts.pop()
    if participants == 0:
        return dict(status='NOT_EXECUTED', participants=0, mean_ticks=None, max_ticks=None, slowest=[])
    maximum = max(item['max_ticks'] for _, _, item in rows)
    return dict(status='EXECUTED', participants=participants,
                mean_ticks=statistics.mean(item['mean_ticks'] for _, _, item in rows), max_ticks=maximum,
                round_mean_ticks=distribution([item['mean_ticks'] for _, _, item in rows]),
                round_max_ticks=distribution([item['max_ticks'] for _, _, item in rows]),
                slowest=[dict(round=n, rank=r, cores=item['slowest_cores'])
                         for n, r, item in rows if item['max_ticks'] == maximum])
