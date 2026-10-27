"""Local HTTP service launcher; integration credentials live outside the model .env."""

import argparse
import secrets
import sys
from pathlib import Path

from pydantic import ValidationError

from ..config import PROJECT_DIR, Settings

DEFAULT_SERVICE_CONFIG = PROJECT_DIR / "runtime" / "service" / "service.env"


def initialize_config(path: Path) -> bool:
    """Create once. Never overwrite a token already copied into a workflow node."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "# Local DC workflow integration. Keep this file private.\n"
        "# The model configuration stays in the original project .env.\n"
        "DC_API_HOST=0.0.0.0\n"
        "DC_API_PORT=8765\n"
        f"DC_API_TOKEN={secrets.token_urlsafe(32)}\n"
        "DC_API_JOB_TIMEOUT=360\n"
        "DC_API_GENERATION_TIMEOUT=300\n"
        "DC_API_POLL_MAX_WAIT=20\n"
        "DC_API_IDEMPOTENCY_TTL=900\n"
        "DC_API_WORKER_POLL_INTERVAL=0.25\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError:
        return False
    # On Windows this preserves the inherited user ACL. On Unix it restricts reads.
    path.chmod(0o600)
    return True


def load_settings(path: Path) -> Settings:
    if not path.is_file():
        raise ValueError("Service configuration is missing; run init-config first")
    settings = Settings(_env_file=(PROJECT_DIR / ".env", path))
    if len(settings.api_token.get_secret_value().strip()) < 24:
        raise ValueError("DC_API_TOKEN must contain at least 24 characters")
    return settings


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local DC workflow HTTP service")
    parser.add_argument("command", choices=("init-config", "serve", "check-config"))
    parser.add_argument("--config", type=Path, default=DEFAULT_SERVICE_CONFIG)
    parser.add_argument("--host", help="Override the configured listen address for this run")
    parser.add_argument("--port", type=int, help="Override the configured listen port for this run")
    args = parser.parse_args(argv)
    path = args.config.resolve()
    if args.command == "init-config":
        created = initialize_config(path)
        print(f"{'Created' if created else 'Kept existing'} service configuration: {path}")
        print("Copy DC_API_TOKEN into the workflow integration node; the token is not printed.")
        return 0
    try:
        settings = load_settings(path)
        overrides = {
            name: value
            for name, value in {"api_host": args.host, "api_port": args.port}.items()
            if value is not None
        }
        if overrides:
            settings = Settings.model_validate({**settings.model_dump(), **overrides})
    except (ValidationError, ValueError, OSError):
        # Validation exceptions can contain configuration values. Do not echo them.
        print("Invalid service configuration. Check the config file, token, host and port.")
        return 2
    if args.command == "check-config":
        print(f"Service configuration valid: {path}")
        print(f"Listen address: {settings.api_host}:{settings.api_port}")
        print("No model request was made.")
        return 0
    import uvicorn

    from .http import create_app

    # One API process owns one persistent worker. Process spawning is guarded by
    # the caller's __main__ entry point, including on Windows.
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port, workers=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
