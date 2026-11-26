"""Build a demo scorecard from synthetic data (no internet): writes demo/index.html."""
import os
import sys
from pathlib import Path

os.environ["TRACKER_OFFLINE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import config  # noqa: E402
from scripts.replay import replay  # noqa: E402


def simulate(days=40, out_dir=None):
    return replay(days, None, out_dir or config.ROOT / "demo",
                  note="Demo with made-up prices and filings, for trying the page offline.")


if __name__ == "__main__":
    print(simulate())
