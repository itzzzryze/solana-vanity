"""Multiprocess CPU search with the same seed sequence and pattern semantics."""
from concurrent.futures import ProcessPoolExecutor
from hashlib import sha256
import signal

from solders.pubkey import Pubkey

from .patterns import prefix_index

_INDEX = None
_GROUPS = None
_PATTERNS = None
_PREFIX = None
_TAILS = None
_OWNER = bytes(32)


def prepare(base, tag, patterns):
    global _INDEX, _GROUPS, _PATTERNS, _PREFIX, _TAILS
    _INDEX, _GROUPS = prefix_index(patterns)
    _PATTERNS = patterns
    _PREFIX = bytes(base) + tag.encode("ascii")
    _TAILS = tuple(pattern.right[::-1][:5] for pattern in patterns)


def init_worker(base, tag, patterns):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    prepare(base, tag, patterns)


def scan(start, length, quotas):
    hits = {}
    remaining = list(quotas)
    active = [0] * _GROUPS
    for index, quota in enumerate(remaining):
        if quota:
            active[index // 32] |= 1 << (index % 32)
    for offset in range(start, start + length):
        digest = sha256(_PREFIX + f"{offset:016x}".encode("ascii") + _OWNER).digest()
        bucket = ((digest[0] << 8) | digest[1]) * _GROUPS
        address = residue = None
        for group in range(_GROUPS):
            mask = _INDEX[bucket + group] & active[group]
            while mask:
                bit = mask & -mask
                mask ^= bit
                index = group * 32 + bit.bit_length() - 1
                tail = _TAILS[index]
                if tail:
                    if residue is None:
                        residue = int.from_bytes(digest, "big") % (58 ** 5)
                    rest = residue
                    for allowed in tail:
                        if not allowed & (1 << (rest % 58)):
                            break
                        rest //= 58
                    else:
                        rest = -1
                    if rest != -1:
                        continue
                if address is None:
                    address = str(Pubkey(digest))
                if _PATTERNS[index].matches(address):
                    hits.setdefault(index, []).append(offset)
                    remaining[index] -= 1
                    if not remaining[index]:
                        active[group] ^= bit
    return hits


class CPU:
    maximum_batch = 262144

    def __init__(self, workers):
        self.workers = workers
        self.name = f"CPU · {workers} worker{'s' if workers != 1 else ''}"
        self.pool = None

    def configure(self, base, tag, patterns):
        self.close()
        args = (bytes(base), tag, patterns)
        if self.workers == 1:
            prepare(*args)
        else:
            self.pool = ProcessPoolExecutor(self.workers, initializer=init_worker, initargs=args)

    def batch(self, start, size, quotas):
        if not self.pool:
            return {index: [value - start for value in values]
                    for index, values in scan(start, size, quotas).items()}
        step = (size + self.workers - 1) // self.workers
        end = start + size
        tasks = [self.pool.submit(scan, offset, min(step, end - offset), quotas)
                 for offset in range(start, end, step)]
        output = {}
        for task in tasks:
            for index, offsets in task.result().items():
                result = output.setdefault(index, [])
                result.extend(value - start for value in offsets[:quotas[index] - len(result)])
        return output

    def close(self):
        if self.pool:
            self.pool.shutdown()
            self.pool = None
