"""User-selected local default and retained Docker switch."""

from dc_agent.config import Settings
from dc_agent.entrypoints.cli import build_service
from dc_agent.infrastructure.execution import DockerRunner, LocalProcessRunner


def test_default_service_uses_current_python_without_docker(tmp_path, monkeypatch):
    monkeypatch.delenv("DC_RUNNER", raising=False)
    monkeypatch.delenv("DC_ALLOW_UNSAFE_LOCAL_EXECUTION", raising=False)

    def reject_docker(*args, **kwargs):
        raise AssertionError("Default local mode must not initialize Docker")

    monkeypatch.setattr("dc_agent.bootstrap.DockerRunner", reject_docker)
    service = build_service(Settings(_env_file=None, runtime_dir=tmp_path))
    try:
        assert isinstance(service.runner, LocalProcessRunner)
        assert service.runner.allow_unsafe is True
        assert service.runner.run("def main(params): return {'code': 0}", {}, b"data", "id") == {
            "code": 0
        }
    finally:
        service.close()


def test_docker_remains_explicitly_selectable(tmp_path, monkeypatch):
    monkeypatch.setenv("DC_RUNNER", "docker")
    monkeypatch.setenv("DC_DOCKER_IMAGE", "custom-runner:v1")
    service = build_service(Settings(_env_file=None, runtime_dir=tmp_path))
    try:
        assert isinstance(service.runner, DockerRunner)
        assert service.runner.image == "custom-runner:v1"
    finally:
        service.close()
