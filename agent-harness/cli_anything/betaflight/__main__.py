"""Enable python -m cli_anything.betaflight."""

import sys

from cli_anything.betaflight.betaflight_cli import main

if __name__ == "__main__":
    sys.exit(main())
