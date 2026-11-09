"""Show automatic flush, background compaction and persistence.

Run from the project root:
    python3 -m demos.storage_lifecycle
    python3 -m demos.storage_lifecycle --data-dir demo_data
"""
import argparse
import json
import tempfile
import time
from pathlib import Path

from lsm_engine import LSMTree
from lsm_engine.lsm_tree import MEMTABLE_LIMIT, L0_COMPACT_TRIGGER


def show_storage(db):
    stats = db.stats()
    print(f'  Memtable: {stats["memtable_entries"]} keys, {stats["memtable_bytes"]:,} bytes')
    print(f'  WAL: {stats["wal_bytes"]:,} bytes')
    for level, info in stats['levels'].items():
        print(f'  L{level}: {info["files"]} SSTable(s), {info["bytes"]:,} bytes')


def demonstrate(directory):
    print(f'Data directory: {directory}')
    value = 'x' * (16 * 1024)
    key_size = len('record:000000')
    per_flush = (MEMTABLE_LIMIT + len(value) + key_size - 1) // (len(value) + key_size)
    count = per_flush * L0_COMPACT_TRIGGER

    with LSMTree(str(directory)) as db:
        print('\n1. Write enough data for four automatic 4 MB flushes.')
        print('   Default durable mode: every WAL record is synced before put returns.')
        for i in range(count):
            db.put(f'record:{i:06d}', value)
            if (i + 1) % per_flush == 0:
                print(f'   After {i + 1:,} writes:')
                show_storage(db)

        print('\n2. Wait for background compaction: L0 -> L1 -> L2.')
        print('   Four flushes contain about 16 MB, exceeding the 10 MB L1 budget.')
        deadline = time.monotonic() + 15
        while True:
            stats = db.stats()
            if (stats['levels'].get(2, {}).get('files', 0)
                    and not stats['levels'].get(0, {}).get('files', 0)
                    and not stats['levels'].get(1, {}).get('files', 0)):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Background compaction did not complete within 15 seconds')
            time.sleep(0.1)
        show_storage(db)
        assert db.get('record:000000') == value
        print('   Verified: the first record remains readable after compaction.')

        print('\n3. Inspect the manifest: it names the committed SSTables.')
        manifest_path = Path(directory) / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        for level, files in enumerate(manifest['levels']):
            if files:
                print(f'   L{level}: {[Path(path).name for path in files]}')
        print('   Commit order: SSTable sync -> rename -> manifest sync -> WAL truncate.')

        print('\n4. Overwrite one disk-backed key and delete another.')
        db.put('record:000000', 'updated')
        db.delete('record:000001')
        assert db.get('record:000000') == 'updated'
        assert db.get('record:000001') is None
        print('   The memtable value and tombstone take priority over older SSTable data.')

    print('\n5. Reopen the database after close has flushed the remaining memtable.')
    with LSMTree(str(directory)) as db:
        assert db.get('record:000000') == 'updated'
        assert db.get('record:000001') is None
        assert db.get(f'record:{count - 1:06d}') == value
        print('   PASS: updated value, deletion, and compacted data survived reopening.')
        show_storage(db)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Keep files in a new or empty directory')
    args = parser.parse_args()
    if args.data_dir:
        directory = args.data_dir.resolve()
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            parser.error('--data-dir must be a new or empty directory')
        demonstrate(directory)
        print(f'\nFiles retained for inspection: {directory}')
    else:
        with tempfile.TemporaryDirectory(prefix='lsmkv_storage_demo_') as directory:
            demonstrate(Path(directory))
        print('\nTemporary demo files cleaned up. Use --data-dir to retain them.')


if __name__ == '__main__':
    main()
