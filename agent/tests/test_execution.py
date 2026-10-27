"""Tests execute only small reviewed fixtures, never model generated code."""

import json
import tempfile
from pathlib import Path

import pytest

from dc_agent.infrastructure.execution import DockerRunner, LocalProcessRunner, RunnerError


def run(code, params=None, timeout=5):
    return LocalProcessRunner(allow_unsafe=True, timeout=timeout).run(
        code, params or {}, b"real bytes", "ABC123"
    )


def test_disabled_by_default():
    with pytest.raises(RunnerError, match="explicit") as error:
        LocalProcessRunner().run("", {}, b"", "source")
    assert error.value.code == "unsafe_execution_disabled"


@pytest.mark.parametrize(
    "expression",
    [
        "requests.get(url).content",
        "requests.request('GET', url).content",
        "requests.Session().get(url).content",
        "urllib.request.urlopen(url).read()",
    ],
)
def test_download_bytes(expression):
    result = run(
        f"""
import requests
import urllib.request
PREFIX = 'http://service-1.example.invalid/api/file/download/'
def main(params):
    url = PREFIX + params['fileMd5']
    return {{'ExcelData': [{expression}.decode()]}}
""",
        {"fileMd5": "ABC123"},
    )
    assert result == {"ExcelData": ["real bytes"]}


@pytest.mark.parametrize(
    "url",
    [
        "http://service-1.example.invalid/api/file/download/OTHER",
        "http://evil.example/ABC123",
        "http://service-1.example.invalid/api/file/download/ABC123?extra=1",
    ],
)
def test_reject_other_download(url):
    with pytest.raises(RunnerError) as error:
        run(f"import requests\ndef main(params):\n requests.get({url!r})\n return {{}}")
    assert error.value.code == "download_denied"


def test_error_and_logs():
    with pytest.raises(RunnerError, match="ValueError: fixture failure"):
        run("def main(params):\n print('noise')\n raise ValueError('fixture failure')")
    assert run("def main(params):\n print('noise' * 100000)\n return {'code': 0}") == {"code": 0}


@pytest.mark.parametrize("value", ["float('nan')", "float('inf')", "{1, 2}"])
def test_strict_json(value):
    with pytest.raises(RunnerError):
        run(f"def main(params):\n return {{'value': {value}}}")


def test_timeout_and_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(RunnerError) as error:
        run("import time\ndef main(params):\n time.sleep(30)\n return {}", timeout=0.2)
    assert error.value.code == "execution_timeout"
    assert list(tmp_path.iterdir()) == []


def test_secret_environment_not_inherited(monkeypatch):
    monkeypatch.setenv("DC_AGENT_TEST_SECRET", "must not be inherited")
    assert run(
        "import os\ndef main(params):\n return {'secret': os.getenv('DC_AGENT_TEST_SECRET')}"
    ) == {"secret": None}


def test_docker_unavailable(monkeypatch):
    monkeypatch.setattr("dc_agent.infrastructure.execution.shutil.which", lambda _: None)
    with pytest.raises(RunnerError) as error:
        DockerRunner().run("", {}, b"", "ABC123")
    assert error.value.code == "runner_unavailable"


def test_docker_restrictions_and_cleanup(monkeypatch):
    calls = []
    paths = []
    monkeypatch.setattr("dc_agent.infrastructure.execution.shutil.which", lambda _: "docker")

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            output_mount = next(arg for arg in command if "target=/output" in arg)
            output = Path(output_mount.split("source=", 1)[1].split(",target=", 1)[0])
            paths.append(output.parent)
            (output / "result.json").write_text(
                json.dumps({"ok": True, "result": {"ExcelData": []}})
            )

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr("dc_agent.infrastructure.execution.subprocess.Popen", Process)
    monkeypatch.setattr("dc_agent.infrastructure.execution.subprocess.run", lambda *a, **kw: None)
    result = DockerRunner().run("def main(params): return {}", {}, b"bytes", "ABC123")
    assert result == {"ExcelData": []}
    command, options = calls[0]
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--user") + 1] == "65534:65534"
    assert {"--read-only", "--cap-drop", "--memory", "--cpus", "--pids-limit"} <= set(command)
    assert all(not path.exists() for path in paths)
    assert "DC_AGENT_TEST_SECRET" not in options["env"]


def test_execution_entrypoint_is_restricted():
    with pytest.raises(RunnerError, match="Unsupported validation entrypoint"):
        LocalProcessRunner(allow_unsafe=True).run(
            "def main(params): return {}", {}, b"data", "id", entrypoint="other_function"
        )
