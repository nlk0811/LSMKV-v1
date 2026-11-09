"""Atomic JSON manifest: the commit point for flush and compaction."""
import json
import os
from ._utils import fsync_fd, fsync_dir

MAX_LEVELS = 7


class Manifest:
    def __init__(self, directory: str):
        self.dir = directory
        self.path = os.path.join(directory, 'MANIFEST.json')
        self._levels = [[] for _ in range(MAX_LEVELS)]
        if os.path.exists(self.path):
            with open(self.path) as source:
                levels = json.load(source)['levels']
            if len(levels) != MAX_LEVELS:
                raise ValueError('MANIFEST must contain seven levels')
            for files in levels:
                for path in files:
                    if not os.path.isfile(path):
                        raise FileNotFoundError(f'MANIFEST references missing SSTable: {path}')
            self._levels = levels

    def _commit(self, levels):
        tmp = self.path + '.tmp'
        with open(tmp, 'w') as target:
            json.dump({'levels': levels}, target, indent=2)
            target.flush()
            fsync_fd(target.fileno())
        os.rename(tmp, self.path)
        fsync_dir(self.dir)
        self._levels = levels

    def save(self):
        self._commit(self._levels)

    def add_l0(self, path):
        levels = [list(files) for files in self._levels]
        levels[0].append(path)
        self._commit(levels)

    def apply_compaction(self, inputs, outputs):
        levels = [list(files) for files in self._levels]
        for level, paths in inputs.items():
            levels[level] = [path for path in levels[level] if path not in paths]
        for level, paths in outputs.items():
            levels[level].extend(paths)
        self._commit(levels)

    @property
    def levels(self):
        return self._levels

    def all_files(self):
        return [path for files in self._levels for path in files]

    def level_size(self, level):
        return sum(os.path.getsize(path) for path in self._levels[level])
