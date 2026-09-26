"""FORKS.md - the log the parallel sessions keep for each other.

WHY

Work on this game happens in several sessions at once, each digging into a
different part. They share one filesystem, so when one of them renames a folder,
changes a file format or moves a generated artefact, the others find out by
breaking - usually much later, and usually while chasing something unrelated.

This is the channel for that, in the same spirit as `symbols/`: a file both
sides read, with a tool that keeps the shape consistent.

WHAT BELONGS HERE

Only what another session could trip over:

    a path or folder that moved
    a file format that changed
    a generated file that is now produced differently, or elsewhere
    a shared tool that gained or lost a flag
    a convention that changed

NOT a diary. "Investigated the flash timing" helps nobody. "The flash patch
addresses now hold different values, regenerate anything derived from them" does.
If another session would not change what it does after reading it, leave it out.

USE

    python mc3_forks.py read              the last few entries
    python mc3_forks.py read --all        everything
    python mc3_forks.py since 2026-08-01  entries from a date on
    python mc3_forks.py add --fork "FORK X" --changed "..." \\
                            --affects "..." --action "..."

Read it when you start, and when something that used to work stops.
"""
import argparse
import datetime
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, 'FORKS.md')

HEAD = """# Fork log

Shared between the parallel sessions working on this game. Newest first.

Each entry answers three questions, in this order: what changed, who it can
break, and what they should do about it. An entry that cannot answer the third
one is usually a diary entry and does not belong here - see `mc3_forks.py`.

Append with:

```
python mc3_forks.py add --fork "YOUR FORK NAME" \\
    --changed "what changed" --affects "who trips over it" --action "what to do"
```

---
"""

ENTRY = re.compile(r'^## (\d{4}-\d{2}-\d{2}) - (.+)$', re.M)


def _load():
    if not os.path.isfile(LOG):
        return HEAD, []
    txt = io.open(LOG, encoding='utf-8').read()
    cut = txt.find('\n---\n')
    if cut < 0:
        return HEAD, []
    head = txt[:cut + 5]
    body = txt[cut + 5:].strip('\n')
    blocks = []
    if body:
        parts = re.split(r'\n(?=## \d{4}-\d{2}-\d{2} - )', body)
        blocks = [p.strip('\n') for p in parts if p.strip()]
    return head, blocks


def add(fork, changed, affects, action, data=None):
    head, blocks = _load()
    day = data or datetime.date.today().isoformat()
    new = ('## %s - %s\n\n'
            '**Changed:** %s\n\n'
            '**Affects:** %s\n\n'
            '**Action:** %s' % (day, fork, changed, affects, action))
    # Newest first: the entry you need is almost always the most recent one, and
    # a log you have to scroll to the bottom of stops being read.
    blocks.insert(0, new)
    io.open(LOG, 'w', encoding='utf-8', newline='\n').write(
        head + '\n' + '\n\n'.join(blocks) + '\n')
    print('%s: %d entries' % (os.path.basename(LOG), len(blocks)))


def read(n=5, all_=False, since=None):
    _head, blocks = _load()
    if not blocks:
        print('%s: empty' % os.path.basename(LOG))
        return
    if since:
        blocks = [b for b in blocks
                  if (ENTRY.search(b) or [None]) and ENTRY.search(b)
                  and ENTRY.search(b).group(1) >= since]
        if not blocks:
            print('no entries since %s' % since)
            return
    elif not all_:
        blocks = blocks[:n]
    for b in blocks:
        print(b)
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='action')

    a = sub.add_parser('add', help='append an entry')
    a.add_argument('--fork', required=True)
    a.add_argument('--changed', required=True)
    a.add_argument('--affects', required=True)
    a.add_argument('--action', required=True)
    a.add_argument('--date')

    r = sub.add_parser('read', help='show recent entries')
    r.add_argument('-n', type=int, default=5)
    r.add_argument('--all', action='store_true')

    s = sub.add_parser('since', help='entries from a date on')
    s.add_argument('data')

    ns = ap.parse_args()
    if ns.action == 'add':
        add(ns.fork, ns.changed, ns.affects, ns.action, ns.date)
    elif ns.action == 'since':
        read(since=ns.data)
    elif ns.action == 'read':
        read(n=ns.n, all_=ns.all)
    else:
        read()
    return 0


if __name__ == '__main__':
    sys.exit(main())
