"""Shared host encoding and result validation for the GPU backends."""
from array import array
from pathlib import Path
from struct import pack, unpack

from .patterns import CAP, prefix_index

SOURCE = Path(__file__).with_name("search.cl")
THREADS = 64
PER_THREAD = 16
RESULT_WIDTH = CAP + 1
PATTERN_WIDTH = 92


class GPUJob:
    def __init__(self, base, tag, patterns):
        self.count = len(patterns)
        table, self.groups = prefix_index(patterns)
        self.buckets = table.tobytes()
        self.data = pack("<12I", *unpack(">12I", bytes(base) + tag.encode("ascii")))
        words = array("I")
        for pattern in patterns:
            tokens = (*pattern.left, *reversed(pattern.right))
            row = [len(pattern.left), len(pattern.right), int(pattern.exact), 0]
            for mask in tokens:
                row.extend((mask & 0xffffffff, mask >> 32))
            words.extend(row + [0] * (PATTERN_WIDTH - len(row)))
        self.patterns = words.tobytes()
        self.output_size = self.count * RESULT_WIDTH * 4

    def active(self, quotas):
        words = [0] * self.groups
        for index, quota in enumerate(quotas):
            if quota:
                words[index // 32] |= 1 << (index % 32)
        return array("I", words).tobytes()

    def results(self, buffer, size, quotas):
        words = memoryview(buffer).cast("I")
        output = {}
        for index, quota in enumerate(quotas):
            pos = index * RESULT_WIDTH
            count = words[pos]
            if count > size or (count and not quota):
                raise RuntimeError("GPU returned an invalid candidate count")
            hits = sorted(words[pos + 1:pos + 1 + min(count, quota)])
            if hits and (hits[-1] >= size or len(set(hits)) != len(hits)):
                raise RuntimeError("GPU returned invalid candidate offsets")
            if hits:
                output[index] = hits
        return output
