"""Generate the model-dependent parts of docs/CHAT_ROUTING.md and its SVG chart.

The chart must not silently go stale when routing models, groups, presets or
deployment routes change, so its model-dependent content is generated from:

- ``config/model_routing.json`` (profile groups and reasoning-effort presets), and
- ``config/inference_policy.production.json`` (which candidates have a deployment
  route, its route class and its review expiry).

Output is deterministic: nothing depends on today's date or the environment.
Models are labelled by their exact provider/model slug.

Usage:
    python scripts/render_chat_routing.py          # rewrite the doc section and SVG
    python scripts/render_chat_routing.py --check  # exit 1 if either is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator.entitlements.policy import parse_inference_policy  # noqa: E402
from orchestrator.model_routing import DEFAULT_PRESET, parse_model_routing  # noqa: E402

ROUTING_PATH = ROOT / "config" / "model_routing.json"
POLICY_PATH = ROOT / "config" / "inference_policy.production.json"
DOC_PATH = ROOT / "docs" / "CHAT_ROUTING.md"
SVG_PATH = ROOT / "docs" / "CHAT_ROUTING.svg"
BEGIN = "<!-- BEGIN GENERATED: chat-routing (scripts/render_chat_routing.py) -->"
END = "<!-- END GENERATED: chat-routing -->"
CHAT_PROFILES = ("routine", "research", "reasoning")
OPENROUTER = "openrouter/"


@dataclass(frozen=True)
class Candidate:
    model: str
    effort: str | None
    route_class: str | None
    review_expires: str | None

    @property
    def label(self) -> str:
        slug = self.model.removeprefix(OPENROUTER)
        effort = self.effort or "provider default"
        text = f"{slug} · {effort}"
        return text if self.route_class else f"{text} (no deployment route)"


@dataclass(frozen=True)
class Group:
    name: str
    candidates: tuple[Candidate, ...]


@dataclass(frozen=True)
class Facts:
    profiles: dict[str, tuple[Group, ...]]

    def lines(self, profile: str) -> list[str]:
        groups = self.profiles[profile]
        if len(groups) == 1 and len(groups[0].candidates) == 1:
            return [groups[0].candidates[0].label, "Only automatic candidate"]
        lines: list[str] = []
        for number, group in enumerate(groups, start=1):
            lines.extend(f"{number}. {candidate.label}" for candidate in group.candidates)
        return lines

    def earliest_expiry(self) -> str | None:
        dates = [
            candidate.review_expires
            for groups in self.profiles.values()
            for group in groups
            for candidate in group.candidates
            if candidate.review_expires
        ]
        return min(dates) if dates else None

    def without_route(self) -> list[str]:
        return sorted(
            {
                candidate.model.removeprefix(OPENROUTER)
                for groups in self.profiles.values()
                for group in groups
                for candidate in group.candidates
                if candidate.route_class is None
            }
        )


def collect(routing_path: Path = ROUTING_PATH, policy_path: Path = POLICY_PATH) -> Facts:
    routing = parse_model_routing(
        json.loads(routing_path.read_text(encoding="utf-8")), source_path=str(routing_path)
    )
    policy = parse_inference_policy(
        json.loads(policy_path.read_text(encoding="utf-8")), source_path=str(policy_path)
    )
    routes: dict[str, tuple[str, str | None]] = {}
    for route in sorted(policy.routes.values(), key=lambda item: item.route_id):
        if route.route_class not in {"routine", "premium"} or route.model in routes:
            continue
        expires = route.review.review_expires_at
        routes[route.model] = (route.route_class, expires.date().isoformat() if expires else None)
    profiles: dict[str, tuple[Group, ...]] = {}
    for name in CHAT_PROFILES:
        groups: list[Group] = []
        for group in routing.profile(name).groups:
            candidates: list[Candidate] = []
            for model in group.models:
                declared = routing.model(model)
                preset: dict[str, object] = {}
                if declared is not None:
                    preset.update(declared.presets.get(DEFAULT_PRESET, {}))
                    preset.update(declared.presets.get(name, {}))
                effort = preset.get("reasoning_effort")
                route_class, expires = routes.get(model, (None, None))
                candidates.append(
                    Candidate(
                        model=model,
                        effort=effort if isinstance(effort, str) else None,
                        route_class=route_class,
                        review_expires=expires,
                    )
                )
            groups.append(Group(name=group.name, candidates=tuple(candidates)))
        profiles[name] = tuple(groups)
    return Facts(profiles=profiles)


def _shared_routine_research(facts: Facts) -> bool:
    return facts.lines("routine") == facts.lines("research")


def render_mermaid(facts: Facts) -> str:
    def node(lines: list[str]) -> str:
        return "<br/>".join(lines).replace('"', "'")

    if _shared_routine_research(facts):
        candidate_nodes = (
            f'    RT --> CRT["Routine and research candidates<br/>{node(facts.lines("routine"))}"]\n'
            "    RS --> CRT\n"
        )
        candidate_ids = ["CRT"]
    else:
        candidate_nodes = (
            f'    RT --> CRT["Routine candidates<br/>{node(facts.lines("routine"))}"]\n'
            f'    RS --> CRS["Research candidates<br/>{node(facts.lines("research"))}"]\n'
        )
        candidate_ids = ["CRT", "CRS"]
    candidate_nodes += (
        f'    RE --> CRE["Reasoning candidate groups<br/>{node(facts.lines("reasoning"))}"]\n'
    )
    candidate_ids.append("CRE")
    into_filter = "".join(f"    {item} --> G\n" for item in ["PIN", *candidate_ids])
    return (
        "```mermaid\n"
        "flowchart TD\n"
        '    U["User chat request"] --> E["Native /chat or<br/>/v1/chat/completions"]\n'
        '    E --> M{"Explicit model selected?"}\n'
        "\n"
        '    M -->|Yes| PIN["Exact requested model<br/>No silent substitution<br/>'
        "Routine scope, model's default effort\"]\n"
        '    M -->|Auto| CL{"Classify the user\'s own text<br/>(not quoted or fenced material)"}\n'
        '    CL -->|"Complexity signal"| RE["Reasoning profile"]\n'
        '    CL -->|"Research signal without complexity"| RS["Research profile"]\n'
        '    CL -->|"Otherwise"| RT["Routine profile"]\n'
        "\n" + candidate_nodes + "\n" + into_filter + "\n"
        '    G["Filter eligible routes<br/>Approval and ZDR policy<br/>Required capabilities and '
        "supported parameters<br/>Context/output fit and account entitlements<br/>Bounded "
        'request cost and available budget"] --> OK{"Eligible route exists?"}\n'
        '    OK -->|No| ERR["Return unavailable / denied result<br/>'
        'Inferred reasoning on native chat: disclosed routine answer"]\n'
        '    OK -->|Yes| SEL["Select first eligible preference group<br/>Honor soft preference, '
        'then lowest bounded cost"]\n'
        '    SEL --> RES["Apply reviewed model preset<br/>Reserve account capacity"]\n'
        '    RES --> LLM["Dispatch pinned endpoint<br/>OpenRouter through LiteLLM"]\n'
        '    LLM --> OUT{"Attempt outcome"}\n'
        '    OUT -->|"Automatic provider failure before any chunk<br/>and another eligible '
        'candidate exists"| NEXT["Settle failed attempt<br/>Try next eligible candidate"]\n'
        "    NEXT --> RES\n"
        '    OUT -->|"Explicit failure, no candidate,<br/>or failure after a chunk"| ERR\n'
        '    OUT -->|Success| PAY["Settle usage"]\n'
        '    PAY --> T{"Tool call requested?"}\n'
        '    T -->|Yes| TOOL["Execute registered tool within limits<br/>Search/fetch, documents, '
        'memory, utilities"]\n'
        '    TOOL -->|"Append result; next model call<br/>within tool-round limit"| G\n'
        '    T -->|No| DONE["Return final chat response<br/>SSE or compatibility JSON"]\n'
        "\n"
        '    LOCAL["Native /local flag"] -.->|"Parsed only; still cloud"| M\n'
        "```\n"
    )


def render_table(facts: Facts) -> str:
    rows = [
        "| Profile | Group | Candidate | Effort | Deployment route | Review expires |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for profile, groups in facts.profiles.items():
        for number, group in enumerate(groups, start=1):
            for candidate in group.candidates:
                rows.append(
                    f"| {profile} | {number}. {group.name} | "
                    f"`{candidate.model.removeprefix(OPENROUTER)}` | "
                    f"{candidate.effort or 'provider default'} | "
                    f"{candidate.route_class or 'none'} | {candidate.review_expires or '—'} |"
                )
    return "\n".join(rows) + "\n"


def render_section(facts: Facts) -> str:
    notes = [
        "Within a group, the lowest bounded cost for the request is tried first; list "
        "order is not priority.",
        "Effort is the preset applied after selection (`default`, overlaid by the "
        "profile's own preset).",
    ]
    missing = facts.without_route()
    if missing:
        notes.append(
            "Candidates without a deployment route are filtered out at dispatch: "
            + ", ".join(f"`{model}`" for model in missing)
            + "."
        )
    expiry = facts.earliest_expiry()
    if expiry:
        notes.append(
            f"The earliest operator review expiry among these deployment routes is {expiry}; "
            "an expired route fails closed."
        )
    notes.append(
        "A route listed here is configuration, not proof of live availability or model quality."
    )
    return (
        f"{BEGIN}\n\n"
        "## Rendered diagram\n\n"
        "![Current Daemon chat routing](CHAT_ROUTING.svg)\n\n"
        "## Mermaid source\n\n"
        + render_mermaid(facts)
        + "\n## Configured chat candidates\n\n"
        + render_table(facts)
        + "\n"
        + "".join(f"- {note}\n" for note in notes)
        + f"\n{END}"
    )


# --------------------------------------------------------------------------- SVG
WIDTH = 1460
LINE = 21
FONT = "Inter, 'Segoe UI', Helvetica, Arial, sans-serif"
STYLES = {
    "plain": ("#ffffff", "#cbd5e1"),
    "decision": ("#fffbeb", "#d97706"),
    "profile": ("#eef2ff", "#6366f1"),
    "core": ("#eff6ff", "#2563eb"),
    "error": ("#fff1f2", "#e11d48"),
    "done": ("#ecfdf5", "#059669"),
}


@dataclass
class Box:
    cx: float
    top: float
    width: float
    lines: list[str]
    style: str = "plain"

    @property
    def height(self) -> float:
        return 22 + LINE * len(self.lines)

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def mid(self) -> float:
        return self.top + self.height / 2

    @property
    def left(self) -> float:
        return self.cx - self.width / 2

    @property
    def right(self) -> float:
        return self.cx + self.width / 2


def _box_svg(box: Box) -> str:
    fill, stroke = STYLES[box.style]
    parts = [
        f'<rect x="{box.left:.0f}" y="{box.top:.0f}" width="{box.width:.0f}" '
        f'height="{box.height:.0f}" rx="9" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
    ]
    first = box.top + 11 + LINE * 0.75
    for index, line in enumerate(box.lines):
        parts.append(
            f'<text x="{box.cx:.0f}" y="{first + index * LINE:.0f}" text-anchor="middle" '
            f'font-size="15" fill="#0f172a">{escape(line)}</text>'
        )
    return "\n".join(parts)


def _path(points: list[tuple[float, float]], color: str = "#64748b", dashed: bool = False) -> str:
    coords = " ".join(f"{x:.0f},{y:.0f}" for x, y in points)
    dash = ' stroke-dasharray="7 6"' if dashed else ""
    marker = {"#64748b": "a-grey", "#dc2626": "a-red", "#7c3aed": "a-violet", "#0284c7": "a-blue"}
    return (
        f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"{dash} '
        f'marker-end="url(#{marker[color]})"/>'
    )


def _label(x: float, y: float, text: str, color: str = "#475569", anchor: str = "middle") -> str:
    lines = text.split("\n")
    return "\n".join(
        f'<text x="{x:.0f}" y="{y + index * 18:.0f}" text-anchor="{anchor}" font-size="13" '
        f'fill="{color}">{escape(line)}</text>'
        for index, line in enumerate(lines)
    )


def render_svg(facts: Facts) -> str:
    center = 727.0
    user = Box(center, 104, 362, ["User chat request"])
    entry = Box(center, 186, 374, ["Native /chat or", "/v1/chat/completions"])
    local = Box(267, 189, 300, ["Native /local flag"])
    explicit = Box(center, 289, 374, ["Explicit model selected?"], "decision")
    pin = Box(273, 398, 400, ["Exact requested model", "Routine scope, model's default effort"])
    classify = Box(879, 404, 400, ["Classify the user's own text"], "decision")
    routine = Box(333, 525, 314, ["Routine profile"], "profile")
    research = Box(757, 525, 314, ["Research profile"], "profile")
    reasoning = Box(1182, 525, 314, ["Reasoning profile"], "profile")

    candidates: list[tuple[Box, list[Box]]] = []
    top = 640.0
    if _shared_routine_research(facts):
        shared = Box(
            545, top, 420, ["Routine and research candidates", *facts.lines("routine")], "profile"
        )
        candidates.append((shared, [routine, research]))
    else:
        candidates.append(
            (
                Box(333, top, 400, ["Routine candidates", *facts.lines("routine")], "profile"),
                [routine],
            )
        )
        candidates.append(
            (
                Box(757, top, 400, ["Research candidates", *facts.lines("research")], "profile"),
                [research],
            )
        )
    pool_width = 560.0 if _shared_routine_research(facts) else 440.0
    pool = Box(
        1430 - pool_width / 2 - 20,
        top,
        pool_width,
        ["Reasoning candidate groups", *facts.lines("reasoning")],
        "profile",
    )
    candidates.append((pool, [reasoning]))
    shift = max(box.bottom for box, _ in candidates) - 736

    def below(y: float) -> float:
        return y + shift

    gate = Box(
        center,
        below(813),
        664,
        [
            "Filter eligible routes",
            "Approval and ZDR policy",
            "Required capabilities and supported parameters",
            "Context/output fit and account entitlements",
            "Bounded request cost and available budget",
        ],
        "core",
    )
    eligible = Box(center, below(986), 374, ["Eligible route exists?"], "decision")
    error = Box(
        212,
        below(980),
        360,
        [
            "Return unavailable / denied result",
            "Inferred reasoning: disclosed routine answer",
        ],
        "error",
    )
    select = Box(
        center,
        below(1077),
        484,
        [
            "Select first eligible preference group",
            "Honor soft preference, then lowest bounded cost",
        ],
    )
    reserve = Box(
        center,
        below(1192),
        484,
        ["Apply reviewed model preset", "Reserve account capacity"],
        "core",
    )
    dispatch = Box(
        center, below(1289), 410, ["Dispatch pinned endpoint", "OpenRouter through LiteLLM"]
    )
    outcome = Box(center, below(1392), 374, ["Attempt outcome"], "decision")
    retry = Box(242, below(1477), 362, ["Settle failed attempt", "Try next eligible candidate"])
    settle = Box(center, below(1513), 362, ["Settle usage"])
    tool_q = Box(center, below(1610), 374, ["Tool call requested?"], "decision")
    tool = Box(
        1152,
        below(1683),
        380,
        ["Execute registered tool within limits", "Search/fetch, documents, memory, utilities"],
    )
    done = Box(
        center,
        below(1761),
        410,
        ["Return final chat response", "SSE or compatibility JSON"],
        "done",
    )
    boxes = [
        user,
        entry,
        local,
        explicit,
        pin,
        classify,
        routine,
        research,
        reasoning,
        *(box for box, _ in candidates),
        gate,
        eligible,
        error,
        select,
        reserve,
        dispatch,
        outcome,
        retry,
        settle,
        tool_q,
        tool,
        done,
    ]

    edges: list[str] = [
        _path([(center, user.bottom), (center, entry.top)]),
        _path([(center, entry.bottom), (center, explicit.top)]),
        _path(
            [
                (local.right, local.mid),
                (461, local.mid),
                (461, explicit.mid),
                (explicit.left, explicit.mid),
            ],
            dashed=True,
        ),
        _label(local.cx, local.bottom + 24, "/local is parsed only; still cloud"),
        _path([(explicit.left, explicit.mid), (pin.cx, explicit.mid), (pin.cx, pin.top)]),
        _label(381, explicit.mid - 12, "Yes — exact selection"),
        _path(
            [
                (center, explicit.bottom),
                (center, explicit.bottom + 28),
                (classify.cx, explicit.bottom + 28),
                (classify.cx, classify.top),
            ]
        ),
        _label(830, explicit.bottom + 22, "Auto"),
        _path(
            [
                (classify.left, classify.mid),
                (655, classify.mid),
                (655, 493),
                (routine.cx, 493),
                (routine.cx, routine.top),
            ]
        ),
        _label(472, 485, "Otherwise"),
        _path(
            [
                (classify.cx, classify.bottom),
                (classify.cx, 493),
                (research.cx, 493),
                (research.cx, research.top),
            ]
        ),
        _label(classify.cx + 10, 487, "Research signal", anchor="start"),
        _path(
            [
                (classify.right, classify.mid),
                (reasoning.cx, classify.mid),
                (reasoning.cx, reasoning.top),
            ]
        ),
        _label(1240, 481, "Complexity signal"),
    ]
    for box, sources in candidates:
        joint = box.top - 26
        for source in sources:
            edges.append(
                _path(
                    [
                        (source.cx, source.bottom),
                        (source.cx, joint),
                        (box.cx, joint),
                        (box.cx, box.top),
                    ]
                )
            )
        edges.append(
            _path(
                [
                    (box.cx, box.bottom),
                    (box.cx, gate.top - 38),
                    (center, gate.top - 38),
                    (center, gate.top),
                ]
            )
        )
    edges += [
        _path([(pin.left + 24, pin.mid), (61, pin.mid), (61, gate.mid), (gate.left, gate.mid)]),
        _path([(center, gate.bottom), (center, eligible.top)]),
        _path([(eligible.left, eligible.mid), (error.right, eligible.mid)], "#dc2626"),
        _label(452, eligible.mid - 12, "No", "#dc2626"),
        _path([(center, eligible.bottom), (center, select.top)]),
        _label(770, eligible.bottom + 22, "Yes"),
        _path([(center, select.bottom), (center, reserve.top)]),
        _path([(center, reserve.bottom), (center, dispatch.top)]),
        _path([(center, dispatch.bottom), (center, outcome.top)]),
        _path(
            [
                (outcome.left, outcome.mid),
                (436, outcome.mid),
                (436, eligible.mid),
                (error.right, eligible.mid),
            ],
            "#dc2626",
        ),
        _label(
            294,
            below(1300),
            "Explicit failure, no candidate,\nor failure after any chunk",
            "#dc2626",
        ),
        _path(
            [
                (center, outcome.bottom + 18),
                (443, outcome.bottom + 18),
                (443, retry.mid),
                (retry.right, retry.mid),
            ],
            "#7c3aed",
        ),
        _label(
            264,
            below(1438),
            "Automatic provider failure before any chunk\n+ another eligible candidate",
            "#7c3aed",
        ),
        _path(
            [
                (retry.left, retry.mid),
                (30, retry.mid),
                (30, reserve.mid),
                (reserve.left, reserve.mid),
            ],
            "#7c3aed",
        ),
        _label(264, below(1176), "Next eligible candidate\nSeparate reservation", "#7c3aed"),
        _path([(center, outcome.bottom), (center, settle.top)]),
        _label(794, outcome.bottom + 52, "Success"),
        _path([(center, settle.bottom), (center, tool_q.top)]),
        _path([(tool_q.right, tool_q.mid), (tool.cx, tool_q.mid), (tool.cx, tool.top)], "#0284c7"),
        _label(1024, tool_q.mid - 12, "Yes — tool call", "#0284c7"),
        _path(
            [(tool.right, tool.mid), (1406, tool.mid), (1406, gate.mid), (gate.right, gate.mid)],
            "#0284c7",
        ),
        _label(
            1267, below(1224), "Next model call\nafter tool result\nwithin round limit", "#0284c7"
        ),
        _path([(center, tool_q.bottom), (center, done.top)]),
        _label(788, tool_q.bottom + 52, "No — final answer"),
    ]

    footer_y = done.bottom + 60
    footer = []
    missing = facts.without_route()
    if missing:
        footer.append(
            "Candidates are not approvals. Without a deployment route: " + ", ".join(missing) + "."
        )
    expiry = facts.earliest_expiry()
    if expiry:
        footer.append(
            f"Deployment policy must be selected explicitly; earliest route review expiry {expiry}."
        )
    footer.append(
        "Generated by scripts/render_chat_routing.py · Not verification of live deployment or model quality."
    )
    height = footer_y + 30 * len(footer) + 20
    markers = "".join(
        f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        f'fill="{color}"/></marker>'
        for name, color in (
            ("a-grey", "#64748b"),
            ("a-red", "#dc2626"),
            ("a-violet", "#7c3aed"),
            ("a-blue", "#0284c7"),
        )
    )
    body = "\n".join(
        [
            f'<rect width="{WIDTH}" height="{height:.0f}" fill="#f8fafc"/>',
            f'<text x="{center:.0f}" y="52" text-anchor="middle" font-size="36" '
            f'font-weight="700" fill="#0f172a" letter-spacing="1">DAEMON · CHAT ROUTING</text>',
            _label(
                center,
                82,
                "Chat only, no Council or background helpers · generated from configuration",
            ),
            *edges,
            *(_box_svg(box) for box in boxes),
            *(_label(center, footer_y + 30 * index, line) for index, line in enumerate(footer)),
        ]
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height:.0f}" '
        f'width="{WIDTH}" height="{height:.0f}" font-family="{FONT}" role="img" '
        f'aria-label="Daemon chat routing">\n<defs>{markers}</defs>\n{body}\n</svg>\n'
    )


# --------------------------------------------------------------------------- CLI
def render_document(current: str, facts: Facts) -> str:
    start, stop = current.find(BEGIN), current.find(END)
    if start == -1 or stop == -1 or stop < start:
        raise SystemExit(f"{DOC_PATH} is missing the generated-section markers")
    return current[:start] + render_section(facts) + current[stop + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the chat routing chart.")
    parser.add_argument("--check", action="store_true", help="fail if outputs are stale")
    args = parser.parse_args(argv)
    facts = collect()
    document = render_document(DOC_PATH.read_text(encoding="utf-8"), facts)
    svg = render_svg(facts)
    stale = [
        path
        for path, expected in ((DOC_PATH, document), (SVG_PATH, svg))
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    if args.check:
        if stale:
            names = ", ".join(str(path.relative_to(ROOT)) for path in stale)
            print(f"Stale: {names}. Run: python scripts/render_chat_routing.py", file=sys.stderr)
            return 1
        return 0
    DOC_PATH.write_text(document, encoding="utf-8")
    SVG_PATH.write_text(svg, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
