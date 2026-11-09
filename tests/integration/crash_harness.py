"""SIGKILL recovery validation: python3 -m tests.integration.crash_harness --runs 5 --keys 20000.

The external log records acknowledged writes after put() returns. Process
termination checks process-crash recovery; it does not simulate power failure.
"""
import argparse
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_WRITER = '''
import sys
sys.path.insert(0, {src!r})
from lsm_engine import LSMTree
with LSMTree({db!r}) as db, open({log!r}, 'w') as log:
    for i in range({n}):
        key = f'key_{{i:08d}}'
        db.put(key, f'val_{{i}}')
        log.write(key + '\\n')
        log.flush()
    # Stay alive so the harness always kills a live process.
    import time
    time.sleep(60)
'''


def run_one(src_dir: str, n_keys: int, kill_after: int, run_id: int) -> dict:
    if not 1 <= kill_after <= n_keys:
        raise ValueError('Require 1 <= kill_after <= n_keys')
    with tempfile.TemporaryDirectory(prefix=f'lsm_crash_{run_id}_') as directory:
        db_path = os.path.join(directory, 'db')
        log = Path(directory) / 'acknowledged.txt'
        script = _WRITER.format(src=src_dir, db=db_path, log=str(log), n=n_keys)
        proc = subprocess.Popen([sys.executable, '-u', '-c', script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        written = set()
        deadline = time.monotonic() + 120
        try:
            while time.monotonic() < deadline:
                if log.exists():
                    written = set(log.read_text().splitlines())
                if len(written) >= kill_after:
                    break
                if proc.poll() is not None:
                    _, error = proc.communicate()
                    return {'error': f'Writer exited early: {error.decode()}',
                            'written': len(written)}
                time.sleep(0.005)
            else:
                return {'error': 'Writer did not reach kill threshold', 'written': len(written)}
            proc.send_signal(signal.SIGKILL)
            proc.communicate(timeout=10)
            if proc.returncode != -signal.SIGKILL:
                return {'error': 'Writer was not terminated by SIGKILL', 'written': len(written)}
            written = set(log.read_text().splitlines())
            missing = corrupt = 0
            from lsm_engine import LSMTree
            with LSMTree(db_path) as db:
                for key in written:
                    expected = f'val_{int(key.split("_")[1])}'
                    value = db.get(key)
                    missing += value is None
                    corrupt += value is not None and value != expected
            return {'written': len(written), 'missing': missing, 'corrupt': corrupt,
                    'pass': bool(written) and missing == 0 and corrupt == 0}
        except Exception as error:
            return {'error': str(error), 'written': len(written)}
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--keys', type=int, default=20_000)
    args = parser.parse_args()
    if args.runs < 1 or args.keys < 1:
        parser.error('--runs and --keys must be positive')
    src = str(Path(__file__).resolve().parents[2])
    passed = 0
    for run in range(1, args.runs + 1):
        kill_at = random.randint(1, args.keys)
        result = run_one(src, args.keys, kill_at, run)
        print(f'Run {run}/{args.runs}, kill_after={kill_at}: {result}', flush=True)
        passed += result.get('pass', False)
    print(f'Result: {passed}/{args.runs} runs passed')
    sys.exit(0 if passed == args.runs else 1)


if __name__ == '__main__':
    main()
