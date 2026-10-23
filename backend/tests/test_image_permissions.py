"""Exercise the verifier as an external script, not stdin or pytest's sys.path."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

VERIFIER = Path(__file__).resolve().parents[2] / "deploy/verify-image-permissions.py"


@pytest.mark.parametrize("cached_decoy", [False, True])
def test_external_script_selects_exact_application_source(tmp_path: Path, cached_decoy: bool) -> None:
    backend = tmp_path / "backend"
    package = backend / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "main.py").write_text("SOURCE = 'canonical'\n")
    decoy = tmp_path / "decoy"
    (decoy / "app").mkdir(parents=True)
    (decoy / "app/__init__.py").write_text("")
    (decoy / "app/main.py").write_text("SOURCE = 'decoy'\n")
    external = tmp_path / "external"
    external.mkdir()
    # A copied verifier plus a script outside WORKDIR reproduces hosted Python's
    # script-path rules. The test process receives neither pytest's import path
    # nor PYTHONPATH, even though its cwd contains the actual application.
    copied_verifier = external / "verify-image-permissions.py"
    copied_verifier.write_bytes(VERIFIER.read_bytes())
    script = external / "probe.py"
    script.write_text(
        "import importlib, runpy, sys\n"
        "from pathlib import Path\n"
        "backend, decoy, verifier, cached = sys.argv[1:]\n"
        "assert backend not in sys.path, sys.path\n"
        "try:\n"
        "    importlib.import_module('app.main')\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('fixture masked the original script-path failure')\n"
        "sys.path.insert(0, decoy)\n"
        "if cached == 'yes':\n"
        "    importlib.import_module('app.main')\n"
        "check = runpy.run_path(verifier)['check_app_import']\n"
        "if cached == 'yes':\n"
        "    try:\n"
        "        check(Path(backend))\n"
        "    except AssertionError as exc:\n"
        "        assert 'unexpected application module' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('incorrect cached app module was accepted')\n"
        "else:\n"
        "    check(Path(backend))\n"
        "    assert importlib.import_module('app.main').SOURCE == 'canonical'\n",
        encoding="utf-8",
    )
    environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(script), str(backend), str(decoy), str(copied_verifier),
         "yes" if cached_decoy else "no"],
        cwd=backend, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
