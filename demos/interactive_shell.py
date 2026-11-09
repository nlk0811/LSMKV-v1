"""Start a terminal with an open database: python3 -m demos.interactive_shell."""
import argparse
import code
from pathlib import Path

from lsm_engine import LSMTree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('demo_data/interactive'),
                        help='Persistent database directory (default: demo_data/interactive)')
    args = parser.parse_args()
    directory = args.data_dir.resolve()
    with LSMTree(str(directory)) as db:
        namespace = {'db': db, 'LSMTree': LSMTree}
        # Use standard terminal editing and db.<Tab> completion when available.
        try:
            import readline
            import rlcompleter
        except ImportError:
            pass
        else:
            readline.set_completer(rlcompleter.Completer(namespace).complete)
            if 'libedit' in (readline.__doc__ or ''):
                readline.parse_and_bind('bind ^I rl_complete')
            else:
                readline.parse_and_bind('tab: complete')
        banner = f'''LSMKV interactive terminal
Database: {directory}
Type commands directly; db is already open.

    db.put("name", "Alice")
    db.get("name")
    db.put("name", "Bob")
    list(db.scan())
    db.delete("name")
    print(db.get("name"))
    db.stats()

Use exit() or Ctrl-D to finish. Data is saved automatically and retained
when you open this shell again. Strings need quotes; put needs a key and value.
'''
        code.interact(banner=banner, local=namespace, exitmsg='')


if __name__ == '__main__':
    main()
