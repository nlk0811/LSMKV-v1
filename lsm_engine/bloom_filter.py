import hashlib
import math
import struct


class BloomFilter:
    """
    Space-efficient probabilistic membership test.
    False positives are possible; false negatives are impossible.

    Used per SSTable so we can skip disk reads for keys that definitely
    don't exist in that SSTable.

    Optimal parameters derived from target capacity and false-positive rate:
        m = -n * ln(p) / (ln 2)^2   (bit array size)
        k = (m/n) * ln 2              (number of hash functions)
    """

    def __init__(self, capacity: int, false_positive_rate: float = 0.01):
        self.capacity = capacity
        self.fpr = false_positive_rate
        self.bit_count = math.ceil(
            -capacity * math.log(false_positive_rate) / (math.log(2) ** 2)
        )
        self.hash_count = max(1, round((self.bit_count / capacity) * math.log(2)))
        self.bits = bytearray(math.ceil(self.bit_count / 8))

    @staticmethod
    def _seed_hashes(key: bytes):
        """Double-hashing seeds from one SHA-256 digest (on-disk compatible)."""
        digest = hashlib.sha256(key).digest()
        return struct.unpack_from('>QQ', digest, 0)

    def add(self, key: bytes):
        h1, h2 = self._seed_hashes(key)
        bits = self.bits
        m = self.bit_count
        for i in range(self.hash_count):
            pos = (h1 + i * h2) % m
            bits[pos >> 3] |= 1 << (pos & 7)

    def may_contain(self, key: bytes) -> bool:
        # Unrolled probe loop: avoid generator/all() overhead on the hot path.
        h1, h2 = self._seed_hashes(key)
        bits = self.bits
        m = self.bit_count
        for i in range(self.hash_count):
            pos = (h1 + i * h2) % m
            if not (bits[pos >> 3] & (1 << (pos & 7))):
                return False
        return True

    # ── serialization ──────────────────────────────────────────────────────────
    # Header: capacity(4I) hash_count(4I) fpr(4f) bit_count(8Q) = 20 bytes
    # bit_count must be stored explicitly — it may not be a multiple of 8,
    # so deriving it from len(bits)*8 on deserialise would shift every hash
    # to a different bucket and produce false negatives.
    _HDR_FMT  = '>IIfQ'
    _HDR_SIZE = struct.calcsize('>IIfQ')   # 20 bytes

    def serialize(self) -> bytes:
        header = struct.pack(self._HDR_FMT,
                             self.capacity, self.hash_count,
                             self.fpr, self.bit_count)
        return header + bytes(self.bits)

    @classmethod
    def deserialize(cls, data: bytes) -> 'BloomFilter':
        hdr_size = struct.calcsize('>IIfQ')
        capacity, hash_count, fpr, bit_count = struct.unpack('>IIfQ', data[:hdr_size])
        bf = cls.__new__(cls)
        bf.capacity   = capacity
        bf.fpr        = fpr
        bf.hash_count = hash_count
        bf.bit_count  = bit_count
        bf.bits       = bytearray(data[hdr_size:])
        return bf
