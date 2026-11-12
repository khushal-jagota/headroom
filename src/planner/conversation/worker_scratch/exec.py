"""Acquire backend ownership, then replace this process with the provider."""

import fcntl
import os
import stat
import sys
from pathlib import Path


def main() -> None:
    directory = Path(sys.argv[1])
    expected = (int(sys.argv[2]), int(sys.argv[3]))
    if directory.parent.is_symlink():
        raise RuntimeError("Worker scratch root changed before exec")
    root_lock = os.open(
        directory.parent / ".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    try:
        root_info = os.fstat(root_lock)
        if not stat.S_ISREG(root_info.st_mode) or root_info.st_uid != os.getuid():
            raise RuntimeError("Worker scratch root lock changed before exec")
        fcntl.flock(root_lock, fcntl.LOCK_EX)
        if directory.is_symlink():
            raise RuntimeError("Worker scratch directory changed before exec")
        descriptor = os.open(
            directory / ".owner", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError("Invalid Worker scratch owner")
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        if (info.st_dev, info.st_ino) != expected:
            raise RuntimeError("Worker scratch ownership changed before exec")
    finally:
        os.close(root_lock)
    os.set_inheritable(descriptor, True)
    command = sys.argv[5:]
    os.execvpe(command[0], command, dict(os.environ))


if __name__ == "__main__":
    main()
