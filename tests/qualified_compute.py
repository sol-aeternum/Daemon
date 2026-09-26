"""Opt-in, account-aware inference policy fixture for legacy endpoint tests.

Production still ships no approved routes; these explicit test routes make
mock-mode chat fixtures exercise the authenticated scope rather than disabling
the runtime guard for the whole suite.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from orchestrator import compute_runtime, main


def install_qualified_compute(monkeypatch, *, models: tuple[str, ...] = ()) -> None:
    selected = models or (
        "openrouter/google/gemini-2.5-flash",
        "openrouter/moonshotai/kimi-k2.5",
        "openrouter/test/explicit-model",
    )
    routes = {}
    for model in selected:
        routes[model] = SimpleNamespace(
            route_id=model,
            route_class="routine",
            model=model,
            provider="openrouter",
            endpoint="https://openrouter.ai/api/v1",
            price_ceiling=SimpleNamespace(
                microusd_per_1m_prompt=0,
                microusd_per_1m_completion=0,
            ),
            max_context_tokens=128000,
            max_output_tokens=4096,
            is_approved=lambda requirements: True,
            supports=lambda *, required_capabilities, input_tokens, output_tokens: (
                required_capabilities <= {"text", "tools"}
                and input_tokens + output_tokens <= 128000
                and output_tokens <= 4096
            ),
            estimate_microusd=lambda prompt, completion: 0,
            transport_payload=lambda requirements: {
                "extra_body": {
                    "provider": {
                        "only": ["test-reviewed"],
                        "order": ["test-reviewed"],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                        "data_collection": "deny",
                        "zdr": True,
                        "max_price": {"prompt": 0, "completion": 0},
                    }
                }
            },
        )
    policy = SimpleNamespace(routes=routes, requirements=object())
    monkeypatch.setattr(compute_runtime, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(main, "load_inference_policy", lambda: policy)

    class Service:
        def __init__(self, pool):
            assert pool is not None

        async def reconcile_expired_reservations(self, user_id, *, before):
            assert isinstance(user_id, uuid.UUID)
            return 0

        async def resolve(self, user_id):
            assert isinstance(user_id, uuid.UUID)
            limits = SimpleNamespace(max_context_tokens=128000, max_output_tokens=4096)
            return SimpleNamespace(
                capabilities={"chat", "extended_agents"},
                limits=limits,
                limits_for=lambda premium: limits,
                remaining_for=lambda premium: 1_000_000,
            )

        async def reserve(self, user_id, amount, **kwargs):
            assert isinstance(user_id, uuid.UUID)
            assert kwargs["model"] in routes
            assert amount == 0
            return uuid.uuid4()

        async def settle(self, reservation, amount, **kwargs):
            assert amount == 0
            return None

    monkeypatch.setattr(compute_runtime, "EntitlementService", Service)
