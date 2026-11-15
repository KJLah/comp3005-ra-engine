"""python3 -m sql parse|compile|check|exec ..."""
import sys

from .cli import main

sys.exit(main())