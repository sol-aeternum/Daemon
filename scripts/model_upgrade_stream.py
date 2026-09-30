"""Observe the production streaming tool loop inside an upgrade experiment.

This adapter delegates every model call to the normal guarded runtime. It adds
durable intent, experiment admission and evidence, without replacing the tool
loop, copying provider protocols, or patching module globals.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from orchestrator import compute_runtime, model_routing
from orchestrator.config import ProviderConfig, get_settings
from orchestrator.tools import completion
from orchestrator.tools.registry import Tool, ToolRegistry
from scripts import model_routing_followup as follow


class FixtureTool(Tool):
    def __init__(
        self, spec: Any, case: follow.CaseFixture, entry: dict[str, Any], cursors: dict[str, int]
    ) -> None:
        self.name = spec.name
        self.description = spec.function["function"]["description"]
        self.parameters = spec.parameters
        self.case, self.entry, self.cursors = case, entry, cursors

    async def execute(self, **kwargs: Any) -> str:
        result = follow.simulate(self.case, self.name, kwargs, self.cursors)
        self.entry["tool_call_count"] += 1
        self.entry["tool_steps"].append({"name": self.name, "arguments": kwargs, "result": result})
        return json.dumps(result)


async def run_stream_attempt(ctx: follow.RunContext, attempt: follow.Attempt) -> None:
    case = ctx.cases[attempt.case_id]
    route = ctx.routes[attempt.candidate_label]
    entry: dict[str, Any] = {
        "attempt_id": attempt.attempt_id,
        "status": "in_progress",
        "calls": [],
        "tool_steps": [],
        "tool_call_count": 0,
        "disallowed_tool_proposals": [],
        "schema_valid": None,
        "requested_reasoning_effort": attempt.effort,
        "final_response": None,
        "task_failures": [],
        "stop": None,
        "stream_observation": {"reasoning_details_chunks": 0, "continuation_details": None},
    }
    ctx.state["attempts"][attempt.attempt_id] = entry
    follow.write_state(ctx.state_path, ctx.state)
    started = time.monotonic()
    deadline = started + follow.ATTEMPT_DEADLINE_S

    async def prepare_dispatch(**params: Any) -> Any:
        follow._self_check(ctx)
        remaining = deadline - time.monotonic()
        follow.ensure_funded_window(ctx.service, ctx.period, remaining + 10)
        index = len(entry["calls"])
        if index >= attempt.max_calls:
            raise follow.TaskFailure("call_budget_exhausted", "streaming call budget exhausted")
        params.update(max_tokens=follow.MAX_OUTPUT_TOKENS, reasoning_effort=attempt.effort)
        follow.require(params["model"] == route.model, "production streaming model drift")
        follow.require(params.get("stream") is True, "production probe must stream")
        follow.require(params.get("tool_choice", "auto") == "auto", "stream tool-choice drift")
        resolved = await ctx.service.resolve(ctx.account)
        bound = follow.verify_candidate(case, route, resolved, params)
        left = attempt.max_calls - index
        await follow.admit_call(ctx, bound + (left - 1) * follow.ceiling_bound(route), left)
        if index:
            assistants = [m for m in params["messages"] if m.get("role") == "assistant"]
            entry["stream_observation"]["continuation_details"] = sum(
                len(m.get("reasoning_details") or []) for m in assistants
            )
        call: dict[str, Any] = {
            "number": index + 1,
            "status": "dispatched_unknown",
            "route_id": route.route_id,
            "requested_model": route.model,
            "provider_pin": list(route.transport.provider_only or ()),
            "reasoning_effort_requested": attempt.effort,
            "max_output_tokens": params["max_tokens"],
            "input_token_bound": compute_runtime._request_bound(params).bound,
            "input_token_estimate": compute_runtime._request_bound(params).estimate,
            "reservation_bound_microusd": bound,
            "account_charge_microusd": None,
            "provider_cost_usd": None,
            "runtime_route_id": None,
            "runtime_model": None,
            "outbound_messages": params["messages"],
        }
        entry["calls"].append(call)
        follow.write_state(ctx.state_path, ctx.state)

        async def observe() -> Any:
            scope: Any = None
            begin = time.monotonic()
            evidence: dict[str, Any] = {"chunks": []}
            try:
                async with compute_runtime.account_compute(
                    ctx.pool,
                    ctx.account,
                    operation="chat",
                    profile="routine",
                    expected_period=ctx.period,
                ) as scope:
                    stream = await compute_runtime.guarded_completion(
                        **params,
                        _route_id=route.route_id,
                        _dispatch_timeout_s=max(0.001, deadline - time.monotonic()),
                    )
                    routed = model_routing.current_routing()
                    call.update(
                        runtime_route_id=routed.selected_route_id,
                        runtime_model=routed.selected_model,
                    )
                    follow.require(
                        routed.selected_route_id == route.route_id
                        and routed.selected_model == route.model,
                        "runtime route drift",
                    )
                    async with completion._closing_stream(stream):
                        async for chunk in stream:
                            raw = follow.live.as_dict(chunk)
                            evidence["chunks"].append(raw)
                            for key in ("model", "provider", "usage"):
                                if raw.get(key) is not None:
                                    evidence[key] = raw[key]
                            for choice in raw.get("choices") or []:
                                details = (choice.get("delta") or {}).get("reasoning_details")
                                if details:
                                    entry["stream_observation"]["reasoning_details_chunks"] += 1
                                if choice.get("finish_reason") is not None:
                                    call["finish_reason"] = choice["finish_reason"]
                            yield chunk
                follow._record_response(call, evidence, route)
                call["status"] = "completed"
                if call.get("finish_reason") in {"length", "content_filter"}:
                    raise follow.TaskFailure(
                        "truncated_response", f"finish_reason={call['finish_reason']!r}"
                    )
            except follow.TaskFailure as exc:
                entry["task_failures"].append({"kind": exc.kind, "detail": exc.detail})
                raise
            except BaseException as exc:
                entry["stop"] = follow.classify_stop(exc)
                raise
            finally:
                call["elapsed_seconds"] = round(time.monotonic() - begin, 6)
                if scope is not None:
                    call["account_charge_microusd"] = (
                        sum(scope.settled.values()) if scope.settled else None
                    )
                call.setdefault("raw_response", evidence)
                follow.write_state(ctx.state_path, ctx.state)

        return observe()

    async def dispatch(**params: Any) -> Any:
        # The application converts ordinary dispatch exceptions to error events.
        # Preserve policy/admission failures before handing them to that loop,
        # just as observe() preserves failures during stream consumption.
        try:
            return await prepare_dispatch(**params)
        except follow.TaskFailure as exc:
            entry["task_failures"].append({"kind": exc.kind, "detail": exc.detail})
            raise
        except BaseException as exc:
            entry["stop"] = follow.classify_stop(exc)
            raise

    registry = ToolRegistry()
    cursors: dict[str, int] = {}
    for tool in case.tools:
        registry.register(FixtureTool(tool, case, entry, cursors))
    provider = ProviderConfig(
        name="openrouter",
        model=route.model,
        base_url=route.endpoint,
        api_key=get_settings().openrouter_api_key,
    )
    events: list[dict[str, Any]] = []
    try:
        await follow._attempt_preflight(ctx)
        async with asyncio.timeout(follow.ATTEMPT_DEADLINE_S):
            loop = completion.completion_with_tools(
                get_settings(),
                provider,
                [{"role": "user", "content": case.prompt}],
                registry,
                actual_model=route.model,
                max_tool_rounds=attempt.max_calls,
                completion_dispatch=dispatch,
            )
            async with completion._closing_stream(loop):
                async for event in loop:
                    events.append(event)
                    if event["type"] == "tool_executing":
                        follow._parse_tool_arguments(
                            case,
                            {
                                "function": {
                                    "name": event["name"],
                                    "arguments": event["arguments"],
                                }
                            },
                        )
                        if entry["tool_call_count"] >= attempt.max_tool_calls:
                            raise follow.TaskFailure("tool_budget_exceeded", "probe tool budget")
        if entry["stop"]:
            raise follow.FollowupError("streaming dispatch stopped; investigation required")
        if any(e["type"] == "error" for e in events) or entry["task_failures"]:
            raise follow.TaskFailure("streaming_failure", "production loop reported failure")
        answers = [e.get("content") for e in events if e["type"] == "content_done"]
        if len(answers) != 1 or not answers[0] or entry["tool_call_count"] != 1:
            raise follow.TaskFailure("streaming_contract", "one tool and final answer required")
        entry["final_response"] = answers[0]
        entry["status"] = "completed"
    except follow.TaskFailure as exc:
        entry["status"] = "failed"
        entry["task_failures"].append({"kind": exc.kind, "detail": exc.detail})
    except BaseException as exc:
        entry["stop"] = entry["stop"] or follow.classify_stop(exc)
        entry["status"] = "stopped"
        raise
    finally:
        entry["events"] = events
        entry["latency_seconds"] = round(time.monotonic() - started, 6)
        follow.write_state(ctx.state_path, ctx.state)
