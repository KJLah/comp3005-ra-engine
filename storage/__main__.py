"""`python3 -m storage ...` runs the Module 1 command line (storage/cli.py)."""
import sys

from .cli import main

sys.exit(main())