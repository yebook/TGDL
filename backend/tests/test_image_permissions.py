"""Exercise the verifier as an external script, not stdin or pytest's sys.path."""

import io
import os
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

import pytest

VERIFIER = Path(__file__).resolve().parents[2] / "deploy/verify-image-permissions.py"
ROOT = VERIFIER.parent.parent


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


@pytest.mark.parametrize("cached_decoy", [False, True])
def test_external_script_selects_exact_api_helper(tmp_path: Path, cached_decoy: bool) -> None:
    library = tmp_path / "api-library"
    library.mkdir()
    (library / "protocol_probe.py").write_text("SOURCE = 'canonical'\n")
    (library / "tgd-api-supervisor.py").write_text("import protocol_probe\nassert protocol_probe.SOURCE == 'canonical'\n")
    (library / "tgd-init-volumes.py").write_text("")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "protocol_probe.py").write_text("SOURCE = 'decoy'\n")
    external = tmp_path / "external"
    external.mkdir()
    script = external / "probe.py"
    script.write_text(
        "import importlib, runpy, sys\n"
        "from pathlib import Path\n"
        "library, decoy, verifier, cached = sys.argv[1:]\n"
        "assert library not in sys.path, sys.path\n"
        "try:\n"
        "    runpy.run_path(str(Path(library) / 'tgd-api-supervisor.py'), run_name='permission_check')\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('fixture masked the external verifier import failure')\n"
        "sys.path.insert(0, decoy)\n"
        "if cached == 'yes':\n"
        "    importlib.import_module('protocol_probe')\n"
        "check = runpy.run_path(verifier)['check_api_import']\n"
        "if cached == 'yes':\n"
        "    try:\n"
        "        check(Path(library))\n"
        "    except AssertionError as exc:\n"
        "        assert 'unexpected API helper module' in str(exc), exc\n"
        "    else:\n"
        "        raise AssertionError('incorrect cached API helper was accepted')\n"
        "else:\n"
        "    check(Path(library))\n"
        "    assert importlib.import_module('protocol_probe').SOURCE == 'canonical'\n",
        encoding="utf-8",
    )
    environment = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(script), str(library), str(decoy), str(VERIFIER),
         "yes" if cached_decoy else "no"],
        cwd=library, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["app", "api"])
def test_final_layer_repairs_restrictive_copy_modes(kind: str) -> None:
    """Opt-in real Docker regression, using local images and no native build.

    Supply TGD_TEST_PERMISSION_APP_IMAGE and TGD_TEST_PERMISSION_API_IMAGE.
    Ordinary COPY deliberately preserves 0700 source files, reproducing the NAS
    observation without relying on COPY --chmod. Execute the actual final-stage
    RUN normalization from the production Dockerfile, then its external verifier.
    """
    base = os.environ.get(f"TGD_TEST_PERMISSION_{kind.upper()}_IMAGE")
    if not base:
        pytest.skip("Set local permission-test image references to run Docker regression")
    dockerfile = ROOT / ("Dockerfile" if kind == "app" else "deploy/bot-api/Dockerfile")
    text = dockerfile.read_text(encoding="utf-8")
    commands = [command for command in re.findall(r"(?m)^RUN chmod[^\n]*", text.replace("\\\n", ""))
                if "/app" in command or "/usr /usr/local" in command]
    assert commands, "Missing final image permission normalization"
    normalization = commands[-1]
    sources = (
        {"deploy/app-entrypoint.py": "/app/entrypoint.py"}
        if kind == "app" else {
            "deploy/bot-api/entrypoint.sh": "/entrypoint.sh",
            "deploy/bot-api/supervisor.py": "/usr/local/lib/tgd-api-supervisor.py",
            "backend/app/services/protocol_probe.py": "/usr/local/lib/protocol_probe.py",
            "deploy/bot-api/init-volumes.py": "/usr/local/lib/tgd-init-volumes.py",
        }
    )
    targets = list(sources.values())
    copies = "\n".join(f"COPY source{index} {target}" for index, target in enumerate(targets))
    before = "import pathlib; " + "; ".join(
        f"assert pathlib.Path({target!r}).stat().st_mode & 0o777 == 0o700" for target in targets
    )
    python = "python" if kind == "app" else "python3"
    denied_target = next(target for target in targets if target.endswith(".py"))
    denied = (
        "import subprocess; "
        f"p=subprocess.run([{python!r}, {denied_target!r}], user=1000, group=1000, capture_output=True); "
        "assert p.returncode != 0 and b'Permission denied' in p.stderr, p.stderr"
    )
    fixture = (
        f"FROM {base}\nUSER 0:0\n{copies}\n"
        "RUN touch /permission-private && chmod 0600 /permission-private\n"
        f"RUN {python} -c \"{before}\"\nRUN {python} -c \"{denied}\"\n{normalization}\n"
        "USER 1000:1000\n"
    )
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, content, mode in [
            ("Dockerfile", fixture.encode(), 0o600),
            *((f"source{index}", (ROOT / source).read_bytes(), 0o700)
              for index, source in enumerate(sources)),
        ]:
            entry = tarfile.TarInfo(name)
            entry.mode, entry.size = mode, len(content)
            tar.addfile(entry, io.BytesIO(content))
    tag = f"tgd-permission-regression:{kind}-{uuid.uuid4().hex}"
    try:
        build = subprocess.run(
            ["docker", "build", "--network=none", "--pull=false", "-t", tag, "-"],
            input=archive.getvalue(), capture_output=True, check=False,
        )
        assert build.returncode == 0, (build.stdout + build.stderr).decode(errors="replace")
        result = subprocess.run(
            ["docker", "run", "--rm", "--network=none", "--read-only",
             "--user=1000:1000", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
             "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", "-e", "TGD_DATA_DIR=/tmp/tgd-check",
             "--mount", f"type=bind,src={VERIFIER},dst=/verify-permissions.py,readonly",
             "--entrypoint", python, tag, "/verify-permissions.py", kind],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        private = subprocess.run(
            ["docker", "run", "--rm", "--network=none", "--read-only", "--user=1000:1000",
             "--cap-drop=ALL", "--entrypoint", python, tag, "-c",
             "import os; assert not os.access('/permission-private', os.R_OK)"],
            capture_output=True, text=True, check=False,
        )
        assert private.returncode == 0, private.stdout + private.stderr
    finally:
        subprocess.run(["docker", "image", "rm", tag], capture_output=True, check=False)
