"""Parse address patterns and build a conservative GPU prefix index."""
from array import array
from dataclasses import dataclass
from itertools import islice
import re

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DIGITS = {char: index for index, char in enumerate(ALPHABET)}
UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ"
LOWER = "abcdefghijkmnopqrstuvwxyz"
ALL = (1 << 58) - 1
BUCKETS = 1 << 16
CAP = 10
_PATTERN_GAP = re.compile(r"\.\.\.|\*")


def character_mask(chars):
    return sum(1 << DIGITS[char] for char in set(chars))


def read_masks(text, ignore_case=False):
    masks = []
    while text:
        if text.startswith("(?i:"):
            end = text.find(")")
            if end < 0:
                raise ValueError("unclosed (?i:...) group")
            masks.extend(read_masks(text[4:end], True))
            text = text[end + 1:]
            continue
        if text.startswith("["):
            end = text.find("]")
            if end < 0:
                raise ValueError("unclosed character class")
            spec = text[1:end]
            if spec in ("CAP", "LOWER", "DIGIT"):
                chars = {"CAP": UPPER, "LOWER": LOWER, "DIGIT": ALPHABET[:9]}[spec]
            elif spec.startswith("CAP^"):
                excluded = spec[4:]
                if not excluded or set(excluded) - set(UPPER):
                    raise ValueError("[CAP^...] requires Base58 capitals to exclude")
                chars = "".join(char for char in UPPER if char not in excluded)
            else:
                chars = spec
            text = text[end + 1:]
        elif text.startswith("?"):
            masks.append(ALL)
            text = text[1:]
            continue
        else:
            chars, text = text[0], text[1:]
        if not chars or set(chars) - DIGITS.keys():
            raise ValueError(f"{chars!r} is empty or contains characters outside Base58")
        if ignore_case:
            chars = set(chars) | (set("".join(chars).swapcase()) & DIGITS.keys())
        masks.append(character_mask(chars))
    return tuple(masks)


@dataclass(frozen=True)
class Pattern:
    label: str
    left: tuple
    right: tuple
    exact: bool

    @classmethod
    def parse(cls, label):
        text = label
        ignore_case = text.startswith("(?i)")
        if ignore_case:
            text = text[4:]
        exact = text.endswith("$")
        if exact:
            text = text[:-1]
        parts = _PATTERN_GAP.split(text)
        if len(parts) > 2 or (exact and len(parts) > 1):
            raise ValueError("use a single gap, or $ for a full-length match")
        left = read_masks(parts[0], ignore_case)
        right = read_masks(parts[1], ignore_case) if len(parts) == 2 else ()
        if not 1 <= len(left) + len(right) <= 44:
            raise ValueError("a pattern must specify between 1 and 44 Base58 positions")
        pattern = cls(label, left, right, exact)
        if (exact and len(left) < 32) or not pattern.intervals():
            raise ValueError("this pattern cannot match a 32-byte Solana address")
        return pattern

    @property
    def identity(self):
        return self.left, self.right, self.exact

    def matches(self, address):
        if len(address) < len(self.left) + len(self.right) or (self.exact and len(address) != len(self.left)):
            return False
        return all(mask & (1 << DIGITS[char]) for mask, char in zip(self.left, address)) and all(
            mask & (1 << DIGITS[char]) for mask, char in zip(reversed(self.right), reversed(address))
        )

    def intervals(self):
        """Project a fixed initial run of letters onto the high 16 digest bits."""
        literal = ""
        for mask in self.left:
            if mask.bit_count() != 1:
                break
            literal += ALPHABET[mask.bit_length() - 1]
        if not literal:
            return [(0, BUCKETS - 1)]
        bounds = []
        for zeros in range(32):
            prefix = "1" * zeros
            low, high = 1 << (8 * (31 - zeros)), 1 << (8 * (32 - zeros))
            if prefix.startswith(literal):
                bounds.append((low, high))
            elif literal.startswith(prefix):
                rest = literal[zeros:]
                if rest.startswith("1"):
                    continue
                value = 0
                for char in rest:
                    value = value * 58 + DIGITS[char]
                for power in islice(POWERS, 45 - len(rest)):
                    start, stop = max(low, value * power), min(high, (value + 1) * power)
                    if start < stop:
                        bounds.append((start, stop))
        if ("1" * 32).startswith(literal):
            bounds.append((0, 1))
        projected = sorted((start >> 240, (stop - 1) >> 240) for start, stop in bounds)
        merged = []
        for start, end in projected:
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged


POWERS = tuple(58 ** exp for exp in range(45))


def prefix_index(patterns):
    groups = (len(patterns) + 31) // 32
    table = array("I", [0]) * (BUCKETS * groups)
    for index, pattern in enumerate(patterns):
        group, bit = divmod(index, 32)
        flag = 1 << bit
        for start, end in pattern.intervals():
            for bucket in range(start, end + 1):
                table[bucket * groups + group] |= flag
    return table, groups


def read_patterns(path):
    patterns = []
    seen = set()
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        label = line.strip()
        if not label or label.startswith("#"):
            continue
        try:
            pattern = Pattern.parse(label)
            if pattern.identity in seen:
                raise ValueError("duplicate pattern")
        except ValueError as error:
            raise ValueError(f"strings.txt line {number} ({label!r}) — {error}") from error
        seen.add(pattern.identity)
        patterns.append(pattern)
    if not 1 <= len(patterns) <= 256:
        raise ValueError("strings.txt needs between 1 and 256 unique patterns")
    return tuple(patterns)
