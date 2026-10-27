"""Run directly from the dc environment without installing this project as a package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dc_agent.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
