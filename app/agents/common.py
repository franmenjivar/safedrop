"""Helpers shared by the three SafeDrop agents."""

from __future__ import annotations

from typing import Any

from pydantic_ai import Agent
from pydantic_ai.settings import ModelSettings

from app.config import REPO_ROOT, SafeDropConfig
from app.services.trajectory_logger import Timer

PROMPT_DIR = REPO_ROOT / "app" / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()


def agent_model(cfg: SafeDropConfig) -> str:
    """The model an agent is constructed with.

    In scripted mode the orchestrator replaces it immediately, so we construct
    against a placeholder that needs no credentials — otherwise the demo mode
    would still require an API key.
    """
    return "test" if cfg.agents.mode == "scripted" else cfg.agents.model


def model_settings(cfg: SafeDropConfig) -> ModelSettings:
    return ModelSettings(temperature=cfg.agents.temperature)


def usage_tokens(result: Any) -> tuple[int, int]:
    """Extract (input, output) token counts from a PydanticAI run result."""
    try:
        usage = result.usage
        if callable(usage):  # older pydantic-ai exposed this as a method
            usage = usage()
        return int(usage.input_tokens or 0), int(usage.output_tokens or 0)
    except Exception:
        return 0, 0


def timed() -> Timer:
    return Timer()


__all__ = ["Agent", "agent_model", "load_prompt", "model_settings", "usage_tokens", "timed"]
