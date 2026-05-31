"""Pytest bootstrap: make the repo root importable so `import kalshi...` works
regardless of where pytest is invoked from."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
