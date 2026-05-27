# Dev subset baseline failure taxonomy

Built from `tests/benchmark_results/dev_subset_baseline/failures.jsonl` and the committed locked canonical runs `run1` + `run2` only.
Phase 0 remains reopened because those two scored runs landed at `32.0%` and `22.0%`, but this taxonomy still uses their locked 39-row union as the baseline failure corpus.

Total classified failures: **39**.

## Classification contract

1. Stage names follow the repo-native intent in `orchestrator/eval/diagnostics.py` (`extraction_miss`, `retrieval_miss`, `reader_failure`), but the plan-requested hyphenated labels are authoritative here: `extraction-miss`, `retrieval-miss`, and `generation-error`.
2. `tests/benchmark_longmemeval/dev_subset.py` treats abstention as an overlay, not a primary `question_type`. To keep a single unique category assignment per failure, every `_abs` row is categorized as `abstention` instead of its raw primary cell.
3. Because the locked artifacts do **not** preserve retrieval-log rows or exact selected-memory snapshots, retrieval vs generation is inferred only after the answer sessions are known to be fully extracted:
   - `extraction-miss`: every failed occurrence still leaves at least one answer session in `extraction_timeout` or `extraction_failed`.
   - `retrieval-miss`: at least one failed occurrence fully extracts the answer sessions, but the hypothesis still says the needed fact is missing, unavailable, or insufficient.
   - `generation-error`: at least one failed occurrence fully extracts the answer sessions, and the hypothesis instead commits to a wrong value/entity/order or a preference-blind partial answer.

## Stage × category matrix

Dense cell threshold: **3+** failures. Percentages are of the full **39**-row corpus; dense cells show up to 3 representative IDs in fixture order.

| Stage \ Category | single-session-user | single-session-assistant | single-session-preference | multi-session | temporal-reasoning | knowledge-update | abstention | Total |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `extraction-miss` | 1 (2.6%) | 4 (10.3%)<br>`e8a79c70`, `71a3fd6b`, `2bf43736` | 1 (2.6%) | 1 (2.6%) | 0 (0.0%) | 2 (5.1%) | 1 (2.6%) | 10 (25.6%) |
| `retrieval-miss` | 6 (15.4%)<br>`8550ddae`, `86f00804`, `19b5f2b3` | 1 (2.6%) | 0 (0.0%) | 6 (15.4%)<br>`ba358f49`, `6cb6f249`, `2318644b` | 5 (12.8%)<br>`8c18457d`, `6613b389`, `gpt4_af6db32f` | 2 (5.1%) | 0 (0.0%) | 20 (51.3%) |
| `generation-error` | 0 (0.0%) | 1 (2.6%) | 2 (5.1%) | 0 (0.0%) | 3 (7.7%)<br>`gpt4_4edbafa2`, `gpt4_65aabe59`, `gpt4_93159ced` | 2 (5.1%) | 1 (2.6%) | 9 (23.1%) |

## Stage totals

| Stage | Count | Share |
| --- | --- | --- |
| `extraction-miss` | 10 | 25.6% |
| `retrieval-miss` | 20 | 51.3% |
| `generation-error` | 9 | 23.1% |

## Category totals

| Category | Count | Share |
| --- | --- | --- |
| `single-session-user` | 7 | 17.9% |
| `single-session-assistant` | 6 | 15.4% |
| `single-session-preference` | 3 | 7.7% |
| `multi-session` | 7 | 17.9% |
| `temporal-reasoning` | 8 | 20.5% |
| `knowledge-update` | 6 | 15.4% |
| `abstention` | 2 | 5.1% |

## Stage assignment buckets

### `extraction-miss` — 10 / 39 (25.6%)

`e8a79c70`, `71a3fd6b`, `28dc39ac`, `gpt4_372c3eed_abs`, `2bf43736`, `0977f2af`, `c5e8278d`, `09d032c9`, `59524333`, `7a8d0b71`

### `retrieval-miss` — 20 / 39 (51.3%)

`8550ddae`, `86f00804`, `19b5f2b3`, `4388e9dd`, `ba358f49`, `6cb6f249`, `2318644b`, `1192316e`, `8c18457d`, `6613b389`, `gpt4_af6db32f`, `gpt4_b0863698`, `5831f84d`, `f685340e`, `ad7109d1`, `0bb5a684`, `92a0aa75`, `545bd2b5`, `cc06de0d`, `25e5aa4f`

### `generation-error` — 9 / 39 (23.1%)

`gpt4_4edbafa2`, `852ce960`, `gpt4_93159ced_abs`, `75f70248`, `c4f10528`, `0a34ad58`, `gpt4_65aabe59`, `3ba21379`, `gpt4_93159ced`

## Category assignment buckets

### `single-session-user` — 7 / 39 (17.9%)

`8550ddae`, `86f00804`, `19b5f2b3`, `ad7109d1`, `c5e8278d`, `545bd2b5`, `25e5aa4f`

### `single-session-assistant` — 6 / 39 (15.4%)

`4388e9dd`, `e8a79c70`, `71a3fd6b`, `2bf43736`, `c4f10528`, `7a8d0b71`

### `single-session-preference` — 3 / 39 (7.7%)

`75f70248`, `0a34ad58`, `09d032c9`

### `multi-session` — 7 / 39 (17.9%)

`28dc39ac`, `ba358f49`, `6cb6f249`, `2318644b`, `1192316e`, `92a0aa75`, `cc06de0d`

### `temporal-reasoning` — 8 / 39 (20.5%)

`8c18457d`, `gpt4_4edbafa2`, `6613b389`, `gpt4_af6db32f`, `gpt4_b0863698`, `0bb5a684`, `gpt4_65aabe59`, `gpt4_93159ced`

### `knowledge-update` — 6 / 39 (15.4%)

`852ce960`, `5831f84d`, `f685340e`, `0977f2af`, `3ba21379`, `59524333`

### `abstention` — 2 / 39 (5.1%)

`gpt4_93159ced_abs`, `gpt4_372c3eed_abs`

## Complete per-question assignments

| Question ID | Stage | Category | Failure runs | Evidence note |
| --- | --- | --- | --- | --- |
| `8550ddae` | `retrieval-miss` | `single-session-user` | `run2` | run2 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have any memories or information about a specific cocktail recipe you tried last weekend.”. |
| `86f00804` | `retrieval-miss` | `single-session-user` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have enough information from the provided memories to determine the specific book you are curren…”. |
| `19b5f2b3` | `retrieval-miss` | `single-session-user` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but there is no information available about your visit to Japan in the provided memories.”. |
| `4388e9dd` | `retrieval-miss` | `single-session-assistant` | `run1`, `run2` | run2 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, I don't have any specific memories of what Andy was wearing in the script for the comedy movie scene.”. |
| `e8a79c70` | `extraction-miss` | `single-session-assistant` | `run2` | Every failed occurrence left at least one answer session unextracted (run2:extraction_timeout), so no failed run preserved fully extracted support for e8a79c70. |
| `71a3fd6b` | `extraction-miss` | `single-session-assistant` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 71a3fd6b. |
| `28dc39ac` | `extraction-miss` | `multi-session` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 28dc39ac. |
| `ba358f49` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have any information about your age or when your friend Rachel is getting married. Therefore, I…”. |
| `6cb6f249` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “Based on the memories provided, there isn't specific information about the total number of days you took social media b…”. |
| `2318644b` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I don't have information on how much you spent on accommodations per night in Tokyo, so I can't calculate the differenc…”. |
| `1192316e` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, I don't have information on your total time for getting ready and commuting to work. If you provide more det…”. |
| `8c18457d` | `retrieval-miss` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have any memories about when you bought a gift for your brother's graduation ceremony. Therefore…”. |
| `gpt4_4edbafa2` | `generation-error` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You made Korean-style BBQ dishes on the 10th of June, but there is no information indicating that you attended a BBQ ev…”. |
| `6613b389` | `retrieval-miss` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but there is no information in the memories regarding when Rachel got engaged, so I cannot determine how man…”. |
| `gpt4_af6db32f` | `retrieval-miss` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but the provided memories do not contain information about the exact date of when you watched the Super Bowl…”. |
| `gpt4_b0863698` | `retrieval-miss` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have enough information to determine how many days ago you participated in the 5K charity run. C…”. |
| `852ce960` | `generation-error` | `knowledge-update` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You were pre-approved for a $350,000 loan.”. |
| `5831f84d` | `retrieval-miss` | `knowledge-update` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have any information about how many Crash Course videos you have watched in the past few weeks.”. |
| `f685340e` | `retrieval-miss` | `knowledge-update` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but there is no information in the provided memories about how often you played tennis with your friends at…”. |
| `gpt4_93159ced_abs` | `generation-error` | `abstention` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You have been working at NovaTech for about 4 years and 3 months before starting your current job at Google.”. |
| `gpt4_372c3eed_abs` | `extraction-miss` | `abstention` | `run2` | Every failed occurrence left at least one answer session unextracted (run2:extraction_timeout), so no failed run preserved fully extracted support for gpt4_372c3eed_abs. |
| `ad7109d1` | `retrieval-miss` | `single-session-user` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have information about your new internet plan speed based on the provided memories.”. |
| `2bf43736` | `extraction-miss` | `single-session-assistant` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 2bf43736. |
| `75f70248` | `generation-error` | `single-session-preference` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “It's possible that your sneezing could be related to something in your living room. Since you are planning to get a spi…”. |
| `0bb5a684` | `retrieval-miss` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “You attended the workshop on 'Effective Communication in the Workplace' on January 10th. Without the specific date of t…”. |
| `0977f2af` | `extraction-miss` | `knowledge-update` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 0977f2af. |
| `c5e8278d` | `extraction-miss` | `single-session-user` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for c5e8278d. |
| `c4f10528` | `generation-error` | `single-session-assistant` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “The restaurant you're thinking of is Miss Bee's.”. |
| `0a34ad58` | `generation-error` | `single-session-preference` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “Since you're planning to visit the Tokyo Tower and are reducing your carbon footprint by using public transport, I reco…”. |
| `92a0aa75` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “Your current role as a Senior Marketing Specialist has not been specified in terms of duration in the provided memories.”. |
| `gpt4_65aabe59` | `generation-error` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You set up the mesh network system first.”. |
| `3ba21379` | `generation-error` | `knowledge-update` | `run2` | run2 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You are currently working on a Ford Mustang Shelby GT350R model.”. |
| `545bd2b5` | `retrieval-miss` | `single-session-user` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have information about your average screen time on Instagram from the provided memories.”. |
| `09d032c9` | `extraction-miss` | `single-session-preference` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 09d032c9. |
| `cc06de0d` | `retrieval-miss` | `multi-session` | `run1`, `run2` | run1 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but I don't have information on the train fare for your commute. Could you provide that detail so I can help…”. |
| `gpt4_93159ced` | `generation-error` | `temporal-reasoning` | `run1`, `run2` | run1 fully extracted the answer sessions, yet the hypothesis still committed to a wrong or preference-blind answer: “You have been working for 4 years and 3 months at NovaTech. If you include your research internship at UC Berkeley, you…”. |
| `59524333` | `extraction-miss` | `knowledge-update` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 59524333. |
| `25e5aa4f` | `retrieval-miss` | `single-session-user` | `run2` | run2 fully extracted the answer sessions, but the hypothesis still treated the needed fact as missing or insufficient: “I'm sorry, but that information is not available in the provided memories.”. |
| `7a8d0b71` | `extraction-miss` | `single-session-assistant` | `run1`, `run2` | Every failed occurrence left at least one answer session unextracted (run1:extraction_timeout, run2:extraction_timeout), so no failed run preserved fully extracted support for 7a8d0b71. |
