"""AEGIS rule: NVIDIA open models only; every LLM role must be a Nemotron model."""
from __future__ import annotations

from .settings import Settings

LLM_ROLES = {"agent", "extractor", "judge", "summariser", "baseline", "attacker", "evaluator"}


class ComplianceError(RuntimeError):
    pass


def check(settings: Settings) -> dict[str, str]:
    """Raise ComplianceError on any non-NVIDIA model; return the resolved role -> model map (for run.json)."""
    for role, model_id in settings.models.items():
        if not model_id.startswith("nvidia/"):
            raise ComplianceError(f"role '{role}' uses non-NVIDIA model '{model_id}'")
        if role in LLM_ROLES and "nemotron" not in model_id.lower():
            raise ComplianceError(f"LLM role '{role}' must be a Nemotron model, got '{model_id}'")
    return dict(settings.models)
