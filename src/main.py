"""Entry point for the disk space menu bar widget."""

import sys
from pathlib import Path

# When launchctl invokes this script directly, sys.path[0] is the src/
# directory, not the project root, so `from src.app import ...` fails.
# Insert the project root so the import works in both invocation modes.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.app import DiskSpaceApp  # noqa: E402


def main() -> None:
    DiskSpaceApp().run()


if __name__ == "__main__":
    main()
