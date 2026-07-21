from __future__ import annotations

import os
import signal
import sys
import time

mode = sys.argv[1]
if mode == "facts":
    payload = "|".join(
        (
            sys.argv[2],
            os.getcwd(),
            os.environ.get("BASE", ""),
            os.environ.get("OVERRIDE", ""),
            os.environ.get("AMBIENT_SENTINEL", "absent"),
        )
    )
    os.write(1, payload.encode())
elif mode == "stream":
    os.write(1, b"one-")
    os.write(2, "é".encode())
    os.write(1, b"-three")
elif mode == "invalid":
    os.write(1, b"a\xffb")
elif mode == "hold":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    os.write(1, b"ready")
    while True:
        time.sleep(0.05)
else:
    raise SystemExit(2)
