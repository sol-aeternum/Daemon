# Task 15 Dream Quality Review

- Review user: `task15-dream-review@daemon.test`
- Trigger path: live Redis enqueue using the same `run_dreaming_job` contract as `/memories/dream` after the HTTP admin gate was found disabled in runtime
- Pre-fix run: `27d2fd88-5754-4223-add9-8bbff4848b3e`
- Post-fix run: `0576fe1e-d1e7-4a2d-ae9f-2cc48d85f15c`

## Pre-fix finding
- Non-obviousness: PASS
- Source linkage: PASS
- Confidence calibration: FAIL
- Hallucination absence: PASS
- Why it failed: both observations were emitted at confidence `1.0` despite depending on only 2-4 supporting memories and involving real synthesis.

## Fix applied
- Scope: `orchestrator/memory/dreaming.py` prompt only
- Change: added explicit confidence-band guidance and reserved `1.0` for overwhelmingly redundant evidence.

## Post-fix finding
- Non-obviousness: PASS
- Source linkage: PASS
- Confidence calibration: PASS
- Hallucination absence: PASS

### Post-fix observations
1. `0.85` — User maintains a detailed coffee journal tracking specific brewing metrics like bloom time and drawdown speed for their pour-over method
2. `0.80` — User prefers brewing coffee at home with a V60, particularly before scheduled activities like work or bike rides

## Final verdict
Dream quality passes after the prompt-only calibration fix. No retrieval-weight, contradiction, entity, or dream-inclusion changes were needed.
