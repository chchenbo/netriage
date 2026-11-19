"""Allow `python3 -m netriage ...` without naming the cli module."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
