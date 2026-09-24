"""Similarity grouping helpers: union-find, a BK-tree for hamming search, simhash."""

import hashlib
import re


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def groups(self):
        """Lists of indices with more than one member."""
        out = {}
        for i in range(len(self.parent)):
            out.setdefault(self.find(i), []).append(i)
        return [g for g in out.values() if len(g) > 1]


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class BKTree:
    """Metric tree over integer hashes for fast 'all within distance d' queries."""

    def __init__(self):
        self.root = None  # (hash, [idx...], {dist: child})

    def add(self, h: int, idx: int):
        if self.root is None:
            self.root = (h, [idx], {})
            return
        node = self.root
        while True:
            d = hamming(h, node[0])
            if d == 0:
                node[1].append(idx)
                return
            child = node[2].get(d)
            if child is None:
                node[2][d] = (h, [idx], {})
                return
            node = child

    def query(self, h: int, max_d: int):
        """Yield indices whose hash is within max_d of h."""
        if self.root is None:
            return
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = hamming(h, node[0])
            if d <= max_d:
                yield from node[1]
            lo, hi = d - max_d, d + max_d
            for cd, child in node[2].items():
                if lo <= cd <= hi:
                    stack.append(child)


# ----------------------------------------------------------- text hashing ----
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalize_words(text: str):
    return _WORD_RE.findall(text.lower())


def shingles(words, k=5):
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _h64(s: str) -> int:
    return int.from_bytes(hashlib.blake2b(s.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(features) -> int:
    """64-bit Charikar simhash of a set of string features."""
    v = [0] * 64
    for f in features:
        h = _h64(f)
        for i in range(64):
            v[i] += 1 if (h >> i) & 1 else -1
    out = 0
    for i in range(64):
        if v[i] > 0:
            out |= 1 << i
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / max(1, len(a | b))
