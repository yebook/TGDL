"""Initialize only private named-volume roots; never touch media or credentials."""

import os
import stat
from pathlib import Path


def initialize(uid: int, gid: int) -> None:
    if not 1 <= uid <= 2147483647 or not 1 <= gid <= 2147483647:
        raise ValueError("TGD_UID and TGD_GID must be nonroot numeric IDs")
    for name, mode in (("/run/tgd-api-control", 0o700), ("/run/tgd-network", 0o770)):
        path = Path(name)
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError("Expected a private volume directory")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISDIR(os.fstat(fd).st_mode):
                raise ValueError("Expected a private volume directory")
            os.fchown(fd, uid, gid)
            os.fchmod(fd, mode)
        finally:
            os.close(fd)


if __name__ == "__main__":
    initialize(int(os.environ.get("TGD_UID", "1000")), int(os.environ.get("TGD_GID", "1000")))
