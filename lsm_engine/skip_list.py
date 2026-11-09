import random
from typing import Optional, Iterator, Tuple

MAX_LEVEL = 16
PROBABILITY = 0.5


class _Node:
    __slots__ = ('key', 'value', 'deleted', 'forward')

    def __init__(self, key, value, level: int, deleted: bool = False):
        self.key = key
        self.value = value
        self.deleted = deleted
        self.forward: list = [None] * (level + 1)


class SkipList:
    """
    Probabilistic sorted linked list used as the in-memory memtable.

    Complexities (average):
        search  O(log n)
        insert  O(log n)
        delete  O(log n)   — inserts a tombstone, does not remove the node
        space   O(n log n)

    Tombstones are propagated down to SSTables so compaction can drop
    deleted keys once no older version of the key exists in lower levels.
    """

    def __init__(self):
        self._head = _Node(None, None, MAX_LEVEL)
        self._level = 0
        self._count = 0
        self._byte_size = 0

    # ── internal ───────────────────────────────────────────────────────────────

    def _random_level(self) -> int:
        lvl = 0
        while random.random() < PROBABILITY and lvl < MAX_LEVEL:
            lvl += 1
        return lvl

    def _find_predecessors(self, key: bytes) -> list:
        update = [self._head] * (MAX_LEVEL + 1)
        node = self._head
        for i in range(self._level, -1, -1):
            while node.forward[i] and node.forward[i].key < key:
                node = node.forward[i]
            update[i] = node
        return update

    # ── public API ─────────────────────────────────────────────────────────────

    def put(self, key: bytes, value: bytes):
        update = self._find_predecessors(key)
        candidate = update[0].forward[0]

        if candidate and candidate.key == key:
            old_val_len = len(candidate.value) if candidate.value else 0
            candidate.value = value
            candidate.deleted = False
            self._byte_size += len(value) - old_val_len
            return

        lvl = self._random_level()
        if lvl > self._level:
            for i in range(self._level + 1, lvl + 1):
                update[i] = self._head
            self._level = lvl

        node = _Node(key, value, lvl)
        for i in range(lvl + 1):
            node.forward[i] = update[i].forward[i]
            update[i].forward[i] = node

        self._count += 1
        self._byte_size += len(key) + len(value)

    def delete(self, key: bytes):
        """Insert or mark a tombstone for key."""
        update = self._find_predecessors(key)
        candidate = update[0].forward[0]

        if candidate and candidate.key == key:
            old_val_len = len(candidate.value) if candidate.value else 0
            candidate.deleted = True
            candidate.value = None
            self._byte_size -= old_val_len
            return

        lvl = self._random_level()
        if lvl > self._level:
            for i in range(self._level + 1, lvl + 1):
                update[i] = self._head
            self._level = lvl

        node = _Node(key, None, lvl, deleted=True)
        for i in range(lvl + 1):
            node.forward[i] = update[i].forward[i]
            update[i].forward[i] = node

        self._count += 1
        self._byte_size += len(key)

    def get(self, key: bytes) -> Optional[Tuple[Optional[bytes], bool]]:
        """Return (value, is_deleted) or None if the key is not present."""
        node = self._head
        for i in range(self._level, -1, -1):
            while node.forward[i] and node.forward[i].key < key:
                node = node.forward[i]
        node = node.forward[0]
        if node and node.key == key:
            return (node.value, node.deleted)
        return None

    def scan(
        self,
        start: Optional[bytes] = None,
        end: Optional[bytes] = None,
    ) -> Iterator[Tuple[bytes, Optional[bytes], bool]]:
        """Yield (key, value, is_deleted) in sorted order within [start, end)."""
        if start:
            node = self._head
            for i in range(self._level, -1, -1):
                while node.forward[i] and node.forward[i].key < start:
                    node = node.forward[i]
            node = node.forward[0]
        else:
            node = self._head.forward[0]

        while node:
            if end and node.key >= end:
                break
            yield (node.key, node.value, node.deleted)
            node = node.forward[0]

    def __iter__(self) -> Iterator[Tuple[bytes, Optional[bytes], bool]]:
        return self.scan()

    def __len__(self) -> int:
        return self._count

    @property
    def size_bytes(self) -> int:
        return self._byte_size
