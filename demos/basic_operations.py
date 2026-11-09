"""Walk through the Version 1 public API: python3 -m demos.basic_operations."""
import tempfile
from lsm_engine import LSMTree


def main():
    with tempfile.TemporaryDirectory(prefix='lsmkv_demo_') as directory:
        with LSMTree(directory) as db:
            db.put('name', 'LSMKV')
            db.put('language', 'Python')
            print('name:', db.get('name'))
            db.put('language', 'Python 3')
            print('updated:', db.get('language'))
            db.delete('language')
            print('deleted:', db.get('language'))
            for i in range(10):
                db.put(f'key{i:02d}', f'value{i}')
            print('range [key03, key07):', list(db.scan('key03', 'key07')))
            print('stats:', db.stats())
        with LSMTree(directory) as db:
            assert db.get('name') == 'LSMKV'
            assert db.get('language') is None
            assert len(list(db.scan())) == 11
            print('Reopen verified: all live keys and deletion persisted.')


if __name__ == '__main__':
    main()
