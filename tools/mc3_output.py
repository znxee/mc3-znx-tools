"""
mc3_output.py - where generated files go

Every tool used to drop its output next to itself, so `tools/experimental/`
ended up with 121 build artefacts sitting among 11 scripts and it was no longer
obvious which files were code and which were the result of running it.

Generated files now land in `tools/output/` unless the caller names a path
explicitly. An explicit `--out-path`/`--out` is always honoured as given: this only
supplies the DEFAULT, so nothing that already passes a path changes behaviour.
"""

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(_HERE, 'output')


def folder():
    """The output directory, created on first use."""
    if not os.path.isdir(DIR):
        os.makedirs(DIR)
    return DIR


def path(name):
    """`nome` resolved inside the output directory.

    A path that is already absolute, or that names a directory of its own, is
    returned untouched - the caller meant it.
    """
    if os.path.isabs(name) or os.path.dirname(name):
        return name
    return os.path.join(folder(), name)
