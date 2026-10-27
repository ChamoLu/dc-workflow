"""Launcher config is repeatable and never prints either credential."""

from dc_agent.entrypoints.server import initialize_config, load_settings, main


def test_init_preserves_existing_token_and_never_prints_it(tmp_path, capsys):
    path = tmp_path / "service" / "service.env"
    assert main(["init-config", "--config", str(path)]) == 0
    settings = load_settings(path)
    token = settings.api_token.get_secret_value()
    assert len(token) >= 32
    assert settings.api_job_timeout == 360
    assert settings.api_generation_timeout == 300
    original = path.read_bytes()
    assert main(["init-config", "--config", str(path)]) == 0
    assert path.read_bytes() == original
    assert main(["check-config", "--config", str(path)]) == 0
    assert token not in capsys.readouterr().out


def test_invalid_config_does_not_echo_secret(tmp_path, capsys):
    path = tmp_path / "service.env"
    path.write_text("DC_API_TOKEN=secret-short\nDC_API_PORT=not-a-port\n", encoding="utf-8")
    assert main(["check-config", "--config", str(path)]) == 2
    assert "secret-short" not in capsys.readouterr().out


def test_config_exclusive_create(tmp_path):
    path = tmp_path / "service.env"
    assert initialize_config(path)
    assert not initialize_config(path)
