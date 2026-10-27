"""Explicit configuration; credentials never enter task payloads or checkpoints."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DC_", env_file=PROJECT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )
    runtime_dir: Path = PROJECT_DIR / "runtime"
    code_dir: Path | None = None
    rule_dir: Path = PROJECT_DIR / "rule"
    todo_filename: str = "todo.xlsx"
    indicator_reference_filename: str = "IndicatorName_Excel_Md.xlsx"
    html_reference_filename: str = "rule_html.xlsx"
    max_rule_bytes: int = Field(default=100_000_000, gt=0)
    model_base_url: str = ""
    model_name: str = ""
    model_api_style: Literal["chat_completions", "responses"] = "chat_completions"
    model_api_key: SecretStr = SecretStr("")
    model_timeout: float = Field(default=240, gt=0)
    model_max_tokens: int = Field(default=12000, gt=0)
    model_design_max_tokens: int = Field(default=4000, gt=0)
    model_generate_max_tokens: int = Field(default=16000, gt=0)
    model_review_max_tokens: int = Field(default=4000, gt=0)
    model_preflight_max_tokens: int = Field(default=800, gt=0)
    preflight_timeout: float = Field(default=90, gt=0)
    model_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    model_reasoning_effort: Literal[
        "", "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = ""
    model_json_mode: bool = True
    model_stream: bool = True
    model_retries: int = Field(default=1, ge=0, le=3)
    model_retry_short_incomplete: bool = True
    max_context_chars: int = Field(default=250000, gt=0)
    task_timeout: float = Field(default=600, gt=0)
    max_repairs: int = Field(default=4, ge=0, le=5)
    max_quality_repairs: int = Field(default=2, ge=0, le=4)
    repair_patience: int = Field(default=2, ge=1, le=5)
    runner: Literal["docker", "local"] = "local"
    docker_image: str = "dc-agent-runner:local"
    runner_timeout: float = Field(default=40, gt=0)
    allow_unsafe_local_execution: bool = True
    download_base_url: str = "http://service-1.example.invalid/api/file/download/"
    download_timeout: float = Field(default=30, gt=0)
    max_source_bytes: int = Field(default=20_000_000, gt=0)
    skill_root: Path | None = None
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8765, ge=1, le=65535)
    api_token: SecretStr = SecretStr("")
    api_job_timeout: float = Field(default=360, gt=0)
    api_generation_timeout: float = Field(default=300, gt=0)
    api_poll_max_wait: float = Field(default=20, ge=0, le=60)
    api_idempotency_ttl: float = Field(default=900, gt=0)
    api_worker_poll_interval: float = Field(default=0.25, gt=0, le=10)

    def resolved_runtime(self) -> Path:
        return (PROJECT_DIR / self.runtime_dir).resolve()

    def resolved_code(self) -> Path:
        # A custom runtime gets a sibling code directory, keeping test/deployment roots isolated.
        if self.code_dir is None:
            return self.resolved_runtime().parent / "code"
        return (PROJECT_DIR / self.code_dir).resolve()

    def resolved_rules(self) -> Path:
        return (PROJECT_DIR / self.rule_dir).resolve()

    def output_budget(self, operation: str) -> int:
        stage = operation.split("_", 1)[0]
        limit = {
            "design": self.model_design_max_tokens,
            "generate": self.model_generate_max_tokens,
            "review": self.model_review_max_tokens,
            "preflight": self.model_preflight_max_tokens,
        }.get(stage, self.model_max_tokens)
        return min(self.model_max_tokens, limit)
