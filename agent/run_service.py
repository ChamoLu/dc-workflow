"""Start the V3 service using the current dc Python environment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dc_agent.entrypoints.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
