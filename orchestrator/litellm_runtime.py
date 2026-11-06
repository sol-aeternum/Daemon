"""Pinned SDK metadata and the narrow OpenRouter reasoning wire adapter.

No import of LiteLLM at module load: the managed launcher checks the environment
before anything can cause LiteLLM's import-time model-table download.
"""

from __future__ import annotations

import os
from typing import Any


class LocalModelCostMapRequired(RuntimeError):
    """A content-free startup refusal; never include SDK source URLs or errors."""


def require_local_model_cost_map() -> None:
    if os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP", "").lower() != "true":
        raise LocalModelCostMapRequired("LiteLLM requires the pinned local model table")
    from litellm.litellm_core_utils.get_model_cost_map import get_model_cost_map_source_info

    info = get_model_cost_map_source_info()
    if not (
        info.get("source") == "local"
        and info.get("is_env_forced") is True
        and info.get("url") is None
        and info.get("fallback_reason") is None
    ):
        raise LocalModelCostMapRequired("LiteLLM requires the pinned local model table")


def openrouter_reasoning_params(params: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate only an OpenRouter effort, after presets and route admission.

    LiteLLM 1.100.4's allowed_openai_params avoids validation but its final
    override can forward an unnormalized `max` for an unknown model. Native
    extra_body survives OpenrouterConfig.transform_request without capability
    lookups. Preserve its max -> xhigh mapping; all other effort strings,
    including none, pass unchanged. include_reasoning remains a separate option,
    as in the SDK's existing wire path. Never mutate the approved provider block.
    Caller parameters still cannot supply extra_body through guarded_completion.
    """
    if not model.startswith("openrouter/") or params.get("reasoning_effort") is None:
        return params
    result = dict(params)
    effort = result.pop("reasoning_effort")
    result["extra_body"] = {
        **result.get("extra_body", {}),
        "reasoning": {"effort": "xhigh" if effort == "max" else effort},
    }
    return result
