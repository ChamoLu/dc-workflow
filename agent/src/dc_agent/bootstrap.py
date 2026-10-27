"""Common application assembly for the existing CLI and isolated HTTP worker."""

from .application.service import AssetService
from .config import Settings
from .generation import SkillRegistry
from .infrastructure import ChatCompletionsGateway, FileSourceProvider, LocalReferences
from .infrastructure.execution import DockerRunner, LocalProcessRunner


def build_service(settings: Settings) -> AssetService:
    runner = (
        LocalProcessRunner(
            allow_unsafe=settings.allow_unsafe_local_execution, timeout=settings.runner_timeout
        )
        if settings.runner == "local"
        else DockerRunner(image=settings.docker_image, timeout=settings.runner_timeout)
    )
    return AssetService(
        settings,
        ChatCompletionsGateway(settings),
        FileSourceProvider(settings),
        LocalReferences(),
        runner,
        SkillRegistry(settings.skill_root),
    )
