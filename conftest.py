"""Pytest path bootstrap so `import refund_abuse` works under `python -I`."""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
