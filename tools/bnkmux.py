#!/usr/bin/env python
"""Entry point: interactive MUX/DEMUX tool for Sony .bnk (989SND/SCREAM) files.

Interactive use:
    python bnkmux.py

Command-line use:
    python bnkmux.py demux file.bnk out_folder [--td file.td]
    python bnkmux.py mux original.bnk out_folder/manifest.json out.bnk [--replace 2=new.genh]
    python bnkmux.py build out.bnk --dir folder_with_genh
    python bnkmux.py build out.bnk --spec spec.json
    python bnkmux.py info file.bnk
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bnk_tool.cli import main

if __name__ == "__main__":
    main()
