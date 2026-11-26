"""Expected fixture chunk and notification boundaries for the requested shapes."""


def chunk_ends(tokens):
    assert tokens in (4, 32, 1024)
    first = min(tokens, 256)
    return [first * i // 4 for i in range(1, 5)] + list(range(512, tokens + 1, 256))


def sync_steps(tokens, mode):
    ends = chunk_ends(tokens)
    if mode == 'physical':
        return list(range(len(ends)))
    if tokens == 1024:
        return [3, 4, 5, 6] if mode == 'logical' else [4, 5, 6]
    return [3]


def legacy_owner(rank, peer, chunk):
    group = (peer // 8 + 4 - rank // 8 % 4) % 4
    position = (0 if group == 0 else 8 + (group - 1) * 16 + peer // 32 * 8)
    relative = (peer - rank) % 8
    position += relative
    parity = max(0, chunk - 3) % 2
    return relative + 8 * parity if position >= 48 else (position + 8) % 16


def cross_signal_chunks(rank, peer, tokens, mode):
    steps = sync_steps(tokens, mode)
    if tokens == 1024 and mode is None and legacy_owner(rank, peer, 3) != legacy_owner(rank, peer, 4):
        return [3, *steps]
    return steps
