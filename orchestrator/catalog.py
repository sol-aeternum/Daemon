from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CatalogModel:
    id: str
    name: str
    tagline: str
    badges: list[str] = field(default_factory=list)
    added_at: float = 0.0


FEATURED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="openrouter/moonshotai/kimi-k2.5",
        name="Kimi K2.5",
        tagline="Deep reasoning, low cost",
        badges=["hot"],
    ),
    CatalogModel(
        id="openrouter/google/gemini-2.5-pro",
        name="Gemini 2.5 Pro",
        tagline="Fast, multimodal",
        badges=[],
    ),
    CatalogModel(
        id="openrouter/openai/gpt-5.2",
        name="GPT-5.2",
        tagline="Strong all-rounder",
        badges=[],
    ),
    CatalogModel(
        id="openrouter/anthropic/claude-opus-4.6",
        name="Claude Opus 4.6",
        tagline="Best for writing & analysis",
        badges=[],
    ),
    CatalogModel(
        id="openrouter/meta-llama/llama-4-scout",
        name="Llama 4 Scout",
        tagline="Open source, fast",
        badges=["new"],
    ),
]


NEW_BADGE_TTL_SECONDS = 14 * 86400


def get_catalog() -> dict[str, object]:
    now = time.time()
    featured: list[dict[str, object]] = []
    for model in FEATURED_MODELS:
        badges = list(model.badges)
        if (
            "new" in badges
            and model.added_at
            and (now - model.added_at > NEW_BADGE_TTL_SECONDS)
        ):
            badges.remove("new")
        featured.append(
            {
                "id": model.id,
                "name": model.name,
                "tagline": model.tagline,
                "badges": badges,
            }
        )

    return {
        "auto": {
            "id": "auto",
            "name": "Auto",
            "tagline": "Intelligent routing based on your message",
            "icon": "zap",
        },
        "featured": featured,
    }
