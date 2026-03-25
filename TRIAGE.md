# Triage Log

> Auto-generated diagnostic capture. Items here were encountered during task
> execution but fall outside the immediate task scope. Review and action as needed.

---

## [2026-03-24T12:52:00Z] — Pytest import path bootstrap broken in conftest
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Running `pytest tests/council/test_integration.py tests/council/test_sse_integration.py tests/test_completion_with_tools.py -q` failed before collection. `tests/conftest.py` imports `orchestrator.config` before adding the project root to `sys.path`, so plain `pytest` crashes with `ModuleNotFoundError`.
- **Evidence**: `ImportError while loading conftest '/home/sol/daemon/tests/conftest.py'. ... tests/conftest.py:8: in <module> from orchestrator.config import get_settings E ModuleNotFoundError: No module named 'orchestrator'`; bootstrap happens later at `tests/conftest.py:13`-`tests/conftest.py:15`.
- **Likely cause**: Import-order regression in `tests/conftest.py`; the path fix executes after the failing import (confidence 98%).
- **Suggested action**: Move the `PROJECT_ROOT`/`sys.path.insert()` bootstrap above `from orchestrator.config import get_settings`, or standardize test execution through `python -m pytest`/`uv run pytest` and document it.
- **Seen again**: 2026-03-25T21:28:23Z during provider-swap Oracle review when `pytest tests/test_video_pricing.py tests/test_fal_kling.py tests/test_kling_e2e.py` failed before collection with the same `ModuleNotFoundError: No module named 'orchestrator'` until `PYTHONPATH=/home/sol/daemon` was injected.

## [2026-03-24T12:54:00Z] — Host Python missing required `trafilatura` dependency
- **Severity**: warning
- **Scope**: host
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: dependency
- **Blocked current task**: no
- **What happened**: After forcing `PYTHONPATH` to continue test collection, pytest still failed because the active Python environment does not have `trafilatura` installed. The dependency is declared in `pyproject.toml`, so this is an environment mismatch rather than missing project metadata.
- **Evidence**: `orchestrator/services/fetch/extract.py:7: import trafilatura` -> `ModuleNotFoundError: No module named 'trafilatura'`; dependency declared at `pyproject.toml:19`.
- **Likely cause**: Tests were run outside the provisioned project environment (`uv`/virtualenv), so declared runtime dependencies were unavailable (confidence 90%).
- **Suggested action**: Run verification via `uv run pytest ...` or sync/install project dependencies into the active interpreter before using plain `pytest`.

## [2026-03-24T12:54:30Z] — LiteLLM emits Python 3.14 deprecation warnings during test collection
- **Severity**: info
- **Scope**: upstream
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: Test collection emitted repeated deprecation warnings from LiteLLM because it still calls `asyncio.iscoroutinefunction`, which Python 3.14 marks for removal in 3.16.
- **Evidence**: `/home/sol/.local/lib/python3.14/site-packages/litellm/litellm_core_utils/logging_utils.py:273: DeprecationWarning: 'asyncio.iscoroutinefunction' is deprecated and slated for removal in Python 3.16; use inspect.iscoroutinefunction() instead` (15 warnings).
- **Likely cause**: LiteLLM has not yet updated this compatibility path for newer Python versions (confidence 95%).
- **Suggested action**: Track LiteLLM release notes for a compatibility fix or pin/document a supported Python version for local testing. Seen again under `uv run pytest` on 2026-03-24.

## [2026-03-24T12:56:30Z] — `run_council()` never persists captured progress events
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: Targeted council tests fail because `run_council()` initializes `progress_events = []` but never appends the emitted events. Output metadata therefore exposes an empty list even though progress was captured and emitted live.
- **Evidence**: `tests/council/test_integration.py::TestCouncilRegressionFixes::test_run_council_metadata_and_progress` failed with `assert []`; code shows `progress_events: list[dict[str, Any]] = []` at `orchestrator/commands/council.py:144` and only `session.metadata["progress_events"] = progress_events` at `orchestrator/commands/council.py:317`, with no `append()` in `capture_progress()`.
- **Likely cause**: Regression during progressive-emission refactor left the metadata buffer disconnected from the callback path (confidence 99%).
- **Suggested action**: Append each event inside `capture_progress()` before `emit_progress()` so metadata and fallback SSE rendering stay consistent.

## [2026-03-24T12:57:00Z] — `council_done` reports zero token totals from output metadata
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: The SSE emitter looks for `metadata["token_costs"]`, but rendered council output stores totals at the top level of `metadata` (`total_tokens`, `total_cost_usd`, `models_used`). As a result, `council_done` emits zeros for totals in tests and likely in production.
- **Evidence**: `tests/council/test_integration.py::TestCouncilRegressionFixes::test_sse_emits_progress_and_raw_sections` failed with `assert 0 == 42`; `orchestrator/council/output.py:112`-`orchestrator/council/output.py:123` writes totals directly into `metadata`, while `orchestrator/council/sse.py:273`-`orchestrator/council/sse.py:285` reads `token_costs = metadata.get("token_costs", {})`.
- **Likely cause**: Contract drift between output renderer and SSE emitter after token-cost metadata was flattened (confidence 98%).
- **Suggested action**: Either nest `token_costs` again in `CouncilOutputRenderer`, or update `_emit_council_output_events()` to read the flattened fields.

## [2026-03-24T12:57:30Z] — Council dataclasses still use deprecated `datetime.utcnow()`
- **Severity**: info
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: deprecation
- **Blocked current task**: no
- **What happened**: `uv run pytest` emitted deprecation warnings from council dataclass defaults that still call `datetime.utcnow()`, which Python 3.14 schedules for removal.
- **Evidence**: Test run showed `<string>:12: DeprecationWarning: datetime.datetime.utcnow() is deprecated ...` and `<string>:7: DeprecationWarning: datetime.datetime.utcnow() is deprecated ...`; affected defaults are in `orchestrator/council/models.py:80`, `orchestrator/council/models.py:96`, and `orchestrator/council/models.py:97`.
- **Likely cause**: Legacy naive-UTC defaults were never migrated to timezone-aware `datetime.now(datetime.UTC)` (confidence 95%).
- **Suggested action**: Replace `datetime.utcnow` defaults/usages with timezone-aware UTC timestamps before Python 3.16.

## [2026-03-24T12:59:00Z] — Council Round 1 exposes side-effecting tools to model outputs
- **Severity**: critical
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: security
- **Blocked current task**: no
- **What happened**: Council Round 1 uses the global default tool registry rather than a read-only council-specific subset. That means the model can emit structured calls for `notification_send`, `reminder_set`, `spawn_agent`, and `spawn_multiple`, causing real side effects or recursive spend amplification.
- **Evidence**: `orchestrator/council/engine.py:34`-`orchestrator/council/engine.py:39` calls `create_default_registry()` and returns all schemas; `orchestrator/tools/builtin.py:151`-`orchestrator/tools/builtin.py:164` registers `NotificationSendTool`, `ReminderSetTool`, `ReminderListTool`, `SpawnAgentTool`, and `SpawnMultipleTool`; `orchestrator/council/prompts.py:111` only mentions search, so these tools are exposed without council-specific guardrails.
- **Likely cause**: The council integration reused the general orchestrator tool registry for convenience instead of whitelisting read-only retrieval tools (confidence 99%).
- **Suggested action**: Replace `create_default_registry()` with a council-specific registry limited to safe, read-only tools such as `web_search`, `web_fetch`, `http_request`, `get_time`, and `calculate`.

## [2026-03-24T12:59:30Z] — Council execution has no session-level token or cost budget cap
- **Severity**: critical
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: performance
- **Blocked current task**: no
- **What happened**: Council accumulates usage only after each model call completes and never aborts when session spend grows too large. With four debate models, up to five tool rounds per model, additional debate rounds, and optional audit, there is no enforcement of the requested 500k-token / $10 ceiling.
- **Evidence**: `orchestrator/council/tools.py:318`-`orchestrator/council/tools.py:391` loops until `max_tool_rounds=5` without any usage threshold checks; `orchestrator/commands/council.py:252`-`orchestrator/commands/council.py:316` totals usage only after rounds complete and stores it in `session.token_costs`; no grep hits for council-side budget/cap constants.
- **Likely cause**: Usage accounting was added for reporting, but no kill-switch was added for enforcement (confidence 98%).
- **Suggested action**: Add a council session budget object checked after every model/tool round, aborting further rounds once either token or dollar limits are exceeded.

## [2026-03-24T13:00:00Z] — `stream_council()` can violate progressive SSE ordering and duplicate final sections
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: The council SSE wrapper has two separate `if result_type == "council_output"` blocks, so final sections can be emitted twice. It also stops draining `progress_queue` as soon as `result_task.done()` becomes true, which can drop the last live progress event before output begins.
- **Evidence**: Duplicate output branches at `orchestrator/council/sse.py:405`-`orchestrator/council/sse.py:414` and `orchestrator/council/sse.py:454`-`orchestrator/council/sse.py:463`; queue drain loop exits at `orchestrator/council/sse.py:359`-`orchestrator/council/sse.py:365` without a final `while progress_queue:` flush after task completion.
- **Likely cause**: Progressive-emission refactor left duplicated tail handling and omitted a final queue flush (confidence 99%).
- **Suggested action**: Deduplicate the terminal result handling and flush any queued progress events once the task completes before emitting output/done events.

## [2026-03-24T13:01:30Z] — Council modules carry extensive basedpyright warnings
- **Severity**: info
- **Scope**: project
- **Encountered during**: [orchestrator/council/*.py and orchestrator/commands/council.py] Read tool loop and emission flow to assess safety checklist — expect evidence for each required finding
- **Category**: other
- **Blocked current task**: no
- **What happened**: LSP diagnostics reported no parser/runtime errors, but `orchestrator/council/engine.py`, `orchestrator/commands/council.py`, and `orchestrator/council/sse.py` carry many basedpyright warnings around `Any`, partially unknown dicts, deprecated imports, and unused placeholders.
- **Evidence**: `lsp_diagnostics` returned large warning sets including `reportExplicitAny`, `reportUnknownVariableType`, `reportDeprecated`, and `reportUnusedVariable` in `orchestrator/council/engine.py`, `orchestrator/commands/council.py`, and `orchestrator/council/sse.py`; `orchestrator/council/tools.py` was clean.
- **Likely cause**: Council integration code was added faster than type coverage and static cleanup kept pace (confidence 92%).
- **Suggested action**: Schedule a follow-up typing/cleanup pass for the council package after the functional safety regressions are addressed.

## [2026-03-24T12:38:55.642Z] — SSE Flush Verification for Council Endpoint
- **Severity**: info
- **Scope**: project
- **Encountered during**: Verify SSE flush is immediate (no buffering) for council endpoint
- **Category**: verification
- **Blocked current task**: no
- **What happened**: Verified that council endpoint in orchestrator/main.py uses StreamingResponse correctly for immediate flushing. No Content-Length header is set (would require buffering), and Transfer-Encoding: chunked is used by default.
- **Evidence**: Code review of orchestrator/main.py lines 1657-1796 showing StreamingResponse usage with proper headers and async generator yielding frames directly.
- **Likely cause**: None - verification passed successfully
- **Suggested action**: None - task completed successfully

## [2026-03-25T21:28:23Z] — Studio video requests are rebilled to default tier and default user
- **Severity**: critical
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: security
- **Blocked current task**: no
- **What happened**: The Studio frontend sends `tier`, `user_id`, `provider`, `reference_image_url`, `kling_model`, and `audio_enabled` inside `metadata.video_generation`, but the backend rebuilds a trusted context that discards most of those fields and hardcodes the billing user/tier. That means actual video runs can be charged/executed against the server default tier and `00000000-0000-0000-0000-000000000001`, not the active Studio selection.
- **Evidence**: `frontend/app/studio/hooks/useVideoGeneration.ts:227`-`frontend/app/studio/hooks/useVideoGeneration.ts:237` sends `tier`, `user_id`, `provider`, `reference_image_url`, `kling_model`, and `audio_enabled`; `orchestrator/main.py:116`-`orchestrator/main.py:186` uses `DEFAULT_BILLING_USER_ID`, `settings.default_tier`, and only forwards `mode`, `duration`, `tier`, `user_id`, `source_mode`, `reference_image_url`, and `reference_image_id`; `orchestrator/tools/spawn.py:232`-`orchestrator/tools/spawn.py:244` propagates the same reduced field set.
- **Likely cause**: The trusted metadata bridge was added as a narrow allowlist and never updated for the Kling provider swap or per-user Studio billing flow (confidence 99%).
- **Suggested action**: Validate and forward the actual Studio `tier`, `user_id`, `provider`, `source_image_url`, `kling_model`, and `audio_enabled` values into `trusted_spawn_context` instead of substituting server defaults.

## [2026-03-25T21:28:23Z] — Frontend Kling identifiers do not match backend provider/model contract
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: config
- **Blocked current task**: no
- **What happened**: The Studio UI uses provider value `kling` and model values `kling-o3-pro` / `kling-v3-pro`, while the backend estimate route and fal client expect provider `fal` and model IDs `o3-pro` / `v3-pro`. As written, Kling estimates return `Invalid provider`, and even if the model reached the client it would fall back to O3 because the identifier is not recognized.
- **Evidence**: `frontend/app/studio/page.tsx:24`-`frontend/app/studio/page.tsx:25` defines `VideoProvider = "xai" | "kling"` and `KlingModel = "kling-v3-pro" | "kling-o3-pro"`; `orchestrator/routes/video_credits.py:158`-`orchestrator/routes/video_credits.py:181` only accepts providers `{"xai", "fal"}`; `providers/fal_kling.py:95`-`providers/fal_kling.py:96` falls back to `o3-pro` for any model not in `["o3-pro", "v3-pro"]`.
- **Likely cause**: Presentation-layer labels leaked into the request contract without a normalization layer between frontend state and backend provider IDs (confidence 99%).
- **Suggested action**: Normalize Studio selections before sending them (`kling` → `fal`, `kling-o3-pro` → `o3-pro`, `kling-v3-pro` → `v3-pro`) or update the backend contract consistently end-to-end.

## [2026-03-25T21:28:23Z] — Kling credit estimation undercharges and ignores some pricing distinctions
- **Severity**: critical
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: Kling pricing is truncated too early, so O3 audio-on and audio-off both charge the same 10 credits for 5 seconds, and V3 audio is flattened to 15 credits for 5 seconds. The code also treats `audio_enabled=True` on `v3-pro` as the higher 0.196/sec voice-control path even though there is no separate voice-control parameter.
- **Evidence**: `config/video_pricing.py:83`-`config/video_pricing.py:96` uses `int(0.112 * 20)`, `int(0.14 * 20)`, and `int(0.196 * 20)` before multiplying by duration; `python -c 'from config.video_pricing import estimate_cost; ...'` returned `o3-no-audio 10`, `o3-audio 10`, `v3-no-audio 10`, `v3-audio 15` for 5-second Pro-tier runs.
- **Likely cause**: Credit conversion was implemented with premature integer truncation and an incomplete pricing model that collapsed `audio_enabled` and voice-control into one branch (confidence 98%).
- **Suggested action**: Rework Kling pricing to preserve fractional credit math until the end, explicitly model V3 audio vs voice-control, and update tests to match the intended price table.

## [2026-03-25T21:28:23Z] — Image-to-video source image is dropped before provider dispatch
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: The Studio flow stores the uploaded reference image under `reference_image_url`, but the image subagent only forwards `source_image_url` to the provider. Because there is no translation between those keys, image-to-video requests lose the source image before the Kling client is called.
- **Evidence**: `frontend/app/studio/hooks/useVideoGeneration.ts:234` sends `reference_image_url`; `orchestrator/main.py:166`-`orchestrator/main.py:185` and `orchestrator/tools/spawn.py:233`-`orchestrator/tools/spawn.py:244` preserve `reference_image_url`; `orchestrator/subagents/image.py:645` reads `context.get("source_image_url")` and `providers/fal_kling.py:106`-`providers/fal_kling.py:107` only uses `source_image_url` / `image_url`.
- **Likely cause**: The earlier Studio reference-image naming was never reconciled with the provider-facing `source_image_url` contract during the swap (confidence 99%).
- **Suggested action**: Standardize on one key name across frontend metadata, trusted context, spawn tools, and provider dispatch, or add an explicit translation at the backend boundary.

## [2026-03-25T21:28:23Z] — Fal Kling provider still depends on ambient `FAL_KEY` despite accepting an injected key
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: test-failure
- **Blocked current task**: no
- **What happened**: `FalKlingProvider` accepts an API key argument, but it instantiates `FalKlingClient()` before applying that key, so the constructor still crashes unless `FAL_KEY` exists in the environment. The client also stores `self.api_key` only for presence checks and never passes it to `fal_client.submit_async()` / `result_async()`.
- **Evidence**: `orchestrator/subagents/image.py:627`-`orchestrator/subagents/image.py:631` passes `fal_api_key` into `FalKlingProvider`; `orchestrator/subagents/image.py:304`-`orchestrator/subagents/image.py:310` creates `FalKlingClient()` before assigning `self.client.api_key = api_key`; `providers/fal_kling.py:50`-`providers/fal_kling.py:52` raises `FalKlingError("FAL_KEY not configured")`; targeted tests failed with `providers.fal_kling.FalKlingError: FAL_KEY not configured` in `tests/test_kling_e2e.py`.
- **Likely cause**: The provider wrapper was adapted from env-only configuration and the injected-key path was never completed (confidence 98%).
- **Suggested action**: Support constructor-based key injection all the way through the client, or clearly remove the unused injected-key path and require/configure env-only auth consistently.

## [2026-03-25T21:28:23Z] — XAI image helper methods were moved off `XAIImageProvider`
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: `XAIImageProvider.generate_image()` still calls `_size_to_aspect_ratio()` and `_aspect_ratio_to_dimensions()`, but those helpers now only exist on `FalKlingProvider`. Any xAI image generation path that reaches those calls will raise `AttributeError`.
- **Evidence**: `orchestrator/subagents/image.py:263` calls `self._size_to_aspect_ratio(size)` and `orchestrator/subagents/image.py:270` calls `self._aspect_ratio_to_dimensions(aspect_ratio)`; the only definitions are `orchestrator/subagents/image.py:352` and `orchestrator/subagents/image.py:382` inside `FalKlingProvider`; `lsp_diagnostics` reported `Cannot access attribute "_size_to_aspect_ratio" for class "XAIImageProvider"` and `Cannot access attribute "_aspect_ratio_to_dimensions" for class "XAIImageProvider"`.
- **Likely cause**: Shared size/aspect helpers were left behind during provider-class refactoring instead of being kept on `XAIImageProvider` or lifted to a common base/helper (confidence 99%).
- **Suggested action**: Move the aspect-ratio helpers to a shared utility/base class or restore them on `XAIImageProvider`.

## [2026-03-25T21:28:23Z] — Specialized video SSE events are defined in frontend but never emitted by backend
- **Severity**: warning
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: runtime-error
- **Blocked current task**: no
- **What happened**: The frontend bridge and event types expect `video_generating`, `video_complete`, and `video_failed`, but the backend streaming path only emits generic `tool_call` / `tool_result` envelopes for media generation. The Studio hook currently works only because it has a `tool_result` fallback parser, not because the dedicated video SSE contract is implemented.
- **Evidence**: `frontend/lib/events.ts:12`-`frontend/lib/events.ts:14` and `frontend/app/api/chat/route.ts:225`-`frontend/app/api/chat/route.ts:272` handle `video_*` events; repo search across `orchestrator/` returned no `sse("video_generating"`, `sse("video_complete"`, or `sse("video_failed"` matches; `orchestrator/daemon.py:457`-`orchestrator/daemon.py:520` only emits `tool_call` and `tool_result` during tool execution.
- **Likely cause**: Frontend event support was added ahead of backend emission wiring, and Kling integration reused the generic tool-result path instead of implementing the dedicated SSE events (confidence 97%).
- **Suggested action**: Either emit the documented `video_*` events from the backend media path or remove the unused contract and rely consistently on typed `tool_result` payloads.

## [2026-03-25T21:28:23Z] — Residual Sora references remain in docs and cached artifacts
- **Severity**: info
- **Scope**: project
- **Encountered during**: [provider swap oracle review] Review complete provider swap for completeness — expect full Kling swap audit across providers/, image subagent, config, studio frontend, and tests
- **Category**: other
- **Blocked current task**: no
- **What happened**: Source code no longer references Sora, but project docs and cached bytecode still do. That means the provider swap is not fully scrubbed from the repository state.
- **Evidence**: `AGENTS.md:23` still lists `OpenAI (Sora)`; `docs/PROJECT_CONTEXT.md:132` still says `OpenAI (Sora video)`; `docs/TECHNICAL_SPECS.md:354` still says `OpenAI (used for Sora video provider paths)`; `providers/__pycache__/` still contains `openai_sora.cpython-311.pyc` and `openai_sora.cpython-314.pyc`.
- **Likely cause**: The swap removed runtime source paths but did not fully update documentation or clean generated artifacts (confidence 96%).
- **Suggested action**: Update the stale docs and remove committed/stale `openai_sora` bytecode artifacts from the repository.

---
