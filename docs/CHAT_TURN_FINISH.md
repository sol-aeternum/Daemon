# Chat turn finish evidence

This contract supports durable continuity (DEC04–DEC05, AC03 and AC09).
It does not qualify a provider, change a plan, or enable durable execution.

`turn_finish` v1 is optional terminal metadata on an assistant message and on
the existing native `final` and `done` events. The Chat Completions endpoint
uses `daemon_turn_finish` on the buffered response or terminal stream chunk.
It separates three facts:

- `outcome` (`complete`, `incomplete`, `failed`, `cancelled`, or `unknown`) and
  a machine-readable `reason` describe the turn.
- `provider` describes only the final answer dispatch. `confirmed` carries a
  known wire reason and `provider_wire` provenance; `missing`, `conflicting`,
  and `not_attempted` do not. A local dispatch identifier is included when
  known. Intermediate tool-call endings and synthetic SDK reasons are not
  final-answer evidence.
- `limits` records observed candidates, with a numeric value and unit when
  known. `binding` means an existing runtime decision confirmed the limit
  stopped this turn. Equal token counts, equal sizing candidates, a fitted
  request, and provider `length` alone do not prove a budget was exhausted.

Standard `stop` means confirmed final provider stop on a completed turn;
`length` means confirmed provider length with an incomplete answer.
`content_filter` describes confirmed filtering. Unknown or unsupported
evidence does not get a fabricated standard reason. Legacy history without
this record remains unknown. Capturing a finish does not stop iteration:
trailing usage, normal end of stream and existing settlement come first.
Native exception frames retain the legacy `final.finish_reason: "error"`
signal alongside `turn_finish`; it is not a provider reason and does not
appear as an OpenAI-compatible finish reason. A live admission refusal
that proves no dispatch reports `provider.state: "not_attempted"`.
The locked OpenRouter adapter recognizes single-choice, identity-encoded
Chat Completions SSE with a complete `[DONE]` event and clean HTTP EOF.
Unsupported streams, including compressed responses, fail closed to unknown;
there is no second request to recover evidence.

For returned non-success answers, the durable writer saves text and evidence
with the original attempt and the existing task/message/attempt terminal
states. Confirmed truncation uses `failed`; missing/conflicting returned-answer
evidence uses `needs_attention` without regeneration. Existing cancellation,
fences, material-effect uncertainty and settlement errors retain precedence.
Failures without a returned terminal answer still use existing retry handling.
Incomplete, failed and unknown answers do not trigger success-only follow-ups.

An authenticated terminal snapshot supersedes provisional live output. If
publication acknowledgement is lost, observation checks for the committed
result under the original identity; it does not retry the publication or
regenerate the answer. Unverifiable results remain unknown. The web client
shows finish notices separately from assistant text.

The isolated restore-history `terminal-v4` extension is a source-only
qualification profile. It is not selected by default startup. Its protected
SQL projection is in the same transaction as the native terminal result, but
the independent history append is not. A durable append without matching SQL
blocks further positive effects and produces recovery-required or unavailable
observation, not a replay grant. See [terminal output restore](TERMINAL_OUTPUT_RESTORE.md)
for the existing completed-only contract.

The Free 4,096-token cap and the existing cost, funding, lease and dispatch
rules are unchanged. The spend-based Free pilot and the HTML rendering issue
are outside this slice of [#759](https://github.com/sol-aeternum/Daemon/issues/759).
