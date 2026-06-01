"""Watcher subprocess entry point.

Launched by `seam start` as:  python -m seam.watcher <db_path> <root_path>

Passing paths as argv (not interpolated into a `python -c` string) avoids
breakage on paths containing spaces, quotes, or backslashes. Logs go to
<db_path.parent>/watcher.log so failures are visible (the parent redirects
this process's stdio to DEVNULL).
"""

import logging
import signal
import sys
import time
from pathlib import Path

import seam.config as config
from seam.watcher.daemon import SeamWatcher


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m seam.watcher <db_path> <root_path>", file=sys.stderr)
        return 2

    db_path = Path(argv[0])
    root_path = Path(argv[1])

    logging.basicConfig(
        level=getattr(logging, config.SEAM_LOG_LEVEL, logging.INFO),
        filename=str(db_path.parent / "watcher.log"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    watcher = SeamWatcher(db_path=db_path, root_path=root_path)
    watcher.start()

    # Stop cleanly on signals so the PID file and observer are torn down.
    def _stop(signum: int, frame: object) -> None:  # noqa: ARG001
        watcher.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Block forever; events are handled on the observer's threads.
    try:
        while True:
            time.sleep(3600)
    finally:
        watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
