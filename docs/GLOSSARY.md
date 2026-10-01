# Glossary

Daemon terms that collide in the codebase or with other products. Use them as defined here in plans, PRs, docs and code comments.

These definitions distinguish product concepts from current implementations; they do not certify capability status. In particular, retention classes describe DEC12's ratified direction, not an implemented field in `config/inference_policy.json`. The runtime remains Z-only pending the separately approved implementation design. See [DAEMON.md](DAEMON.md), [DEC12](DAEMON_VISION_DECISIONS.md#dec12--zdr-by-default-provider-retained-routes-opt-in-only) and [FEATURE_MATRIX.md](FEATURE_MATRIX.md).

| Term | Means | Does not mean |
|---|---|---|
| **Daemon** | The product, and the coordinating agent that handles each request | Any single model |
| **device** | An authenticated client session (`devices`, `sessions`, `/enroll/*`, migration 031) | A connected device. Always write "connected device" for V04 companions |
| **enrollment** | Auth client enrolment (`pending_enrollments`) | QR connected-device pairing (vision §4.3) |
| **connected device** | An optional, permission-scoped companion on a user's computer (V04–V05; not implemented) | A client session |
| **agent / subagent** | Daemon's runtime subagents (@research, Council, future coding agents) | OpenCode build roles (Prometheus, Atlas…). Call those "build roles" |
| **workspace** | The user's single persistent Daemon environment (DEC01) | An IDE, or the `/projects` placeholder UI |
| **project** | An optional scope inside the workspace. Shared by default, or restricted (DEC01–DEC03) | `memories.category='project'`, or "Project Daemon" |
| **task** | An accepted request with durable, tracked status (DEC05) | A chat stream, or an arq job |
| **attempt / run** | One execution attempt of a task | A new task |
| **artifact** | A durable, owned output with identity and lineage | The current interactive HTML render or gallery item, or a file under `data/generated_*` that expires after 24h |
| **role** | A routing slot (fast, orchestration, reasoning, coding, research, extraction, media) mapped to a model in config. The code implements roles as workload profiles in `config/model_routing.json`: `routine` (the fast role), `research`, `reasoning`, `background` and `council`; the other listed roles are not yet configured profiles | A commercial plan |
| **plan** | A commercial plan: Free, Pro or Power | Memory tier, or a Sisyphus PLAN.md |
| **tier** | Memory L0/L1/L2 | A commercial plan, or a workload role. `orchestrator/model_router.py` still names fast/reasoning selection a `tier` (`select_model_tier`); in prose call that a role |
| **route** | A qualified provider + model + endpoint in `config/inference_policy.json`, plus a DEC12 retention class (not yet a config field) | A model catalogue entry |
| **retention class** | Z (zero retention, default), R (provider-retained, opt-in), T (trains; never used). See DEC12 | A plan feature |
| **local** | Local inference (`/local`, parked) | Resources on a connected device |
| **reconnect** | Currently re-runs the request (`regenerate()`) | Resuming a task. That arrives with Stage 1 |
