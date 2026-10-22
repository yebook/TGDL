"""Release metadata shipped with the application, independent of Git and environment."""
import json
import re
from pathlib import Path


def load_release(path: Path) -> str:
    metadata = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(metadata, dict) or set(metadata) != {"version"}
            or not isinstance(metadata["version"], str)
            or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", metadata["version"]) is None):
        raise ValueError("Invalid release metadata")
    return metadata["version"]


VERSION = load_release(Path(__file__).with_suffix(".json"))
