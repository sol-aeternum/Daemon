# Frontend Hooks compatibility maintenance

This is a source-maintenance contract, not a feature promotion or deployment
record. The upgraded Hooks lint rules remain enabled without suppressions.

## Preserved boundaries

- Initial loader presentation is initialized separately from event-driven
  loading feedback. Request, controller and foreground ownership are claimed
  synchronously before authentication awaits. Publication and finalization follow
  the real request promise and retain their existing lifetime checks.
- Google sign-in presentation follows its setup inputs; credential intent,
  generation, abort, SDK cleanup and readiness-before-navigation stay owned by
  effects and events. Returning from client A through B to A does not revive the
  original A setup, callback or completion.
- Protected media presentation is qualified by raw URL, not by auth generation.
  Auth retirement revokes its owned blob URL without automatically fetching the
  same retained URL for a replacement account. Completion and unrelated renders
  do not revoke an accepted blob; public URLs remain borrowed.
- Chat uses the SDK's public transport methods, with construction at request
  time. Suggestion dispatch refs remain synchronous owners; presentation state
  does not replace them. Before route promotion, Stop uses the existing
  SSE-assigned destination and promotes provisional markers without transferring
  an unrelated draft.
- Archive enrichment is pure, idempotent and persisted. Only already archived
  material operations may be enriched; reset archives stay empty, and conflicting
  operation identity, name or outcome cannot replace saved content.
- Conversation loading retains permanent/exhausted failures and bounded retry
  cleanup. Home clears selected content and failure; non-null navigation preserves
  the existing content transition while clearing the previous load failure.
- Memory stats preserve the existing distinction: non-OK responses clear stats,
  while thrown/network/parse failures retain previous stats. Paging, mutation
  sequencing and embedding-status ownership are not replaced by donor algorithms.

## Regression anchors and qualification limits

`auth-landing.test.tsx` covers client A→B→A with held old challenges, stale
callbacks and a late completion. `suggestion-page-integration.test.tsx` composes
the real page with the real Stop hook for Home and an empty existing conversation:
Stop before assignment, after SSE assignment before the route changes, and after
route promotion, including navigation away/back and draft preservation. Its
transport test exercises the real SDK send path after the real cache-readiness
barrier receives a synthetic acknowledgement; it does not mock away auth refusal.

Media lifetime, archive reset/conflict, stale conversation load, auth action-owner,
retained-source and memory paging suites remain required regression evidence.
These are synthetic tests, not exhaustive interleaving or production-browser
qualification. No timeout, fixture-global-setup, security rule or default browser
inventory relaxation is part of this maintenance.

The canonical local test interpreter and Docker interpreter are distinct. Passing
the unchanged real backend setup in the canonical local environment does not
prove why an older environment timed out or qualify Docker execution. Full local
CI, security/cache/browser/build checks and exact-head hosted and human review
remain separate release requirements.
