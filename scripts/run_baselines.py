#!/usr/bin/env python3
"""Backward-compatible: runs `baseline.py`. Prefer: python scripts/baseline.py"""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).resolve().with_name("baseline.py")), run_name="__main__")
