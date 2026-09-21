# GameEditor: XGE versus UBA

Collected on 2026-09-18 using authenticated Jenkins MCP tools. Source job: [payday3/trunk/GameEditor](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/).

## Windows and coverage

- Frozen historical window: September 8–17, 2026 inclusive (the ten complete calendar days before collection), Atlantic/Canary timezone. JSON timestamps are UTC; do not interpret agent-local log timestamps as UTC.
- Prospective UBA window: September 19–28, 2026 inclusive, Atlantic/Canary timezone. Ten calendar days does not guarantee ten Nightly builds.
- September 17 and 18 already used UBA. Preserve these as `uba_precollection`; do not pool them into either the XGE baseline or the prospective UBA sample.
- Five successful XGE builds have verified Nightly ancestry, clean execution and primary-executor evidence. All seven retained observations ran on `jk-win-004`.
- September 8: Nightly #85 triggered FullBuild #254. Its console reports `payday3/trunk/GameEditor #770 completed with status ABORTED`. Direct GameEditor access returned `Job not found`; executor and timing are unverified. Excluded.
- September 9: Nightly #86 triggered FullBuild #257. Its console reports Game Editor #779 `CLEAN 991112 → 991148` completed successfully. Direct GameEditor status/log access returned `Job not found`, and the stages API returned no stages. Executor and detailed timings are unavailable. Excluded from the verified baseline.
- No September 12 or 13 Nightly entries appeared between Nightly #88 (September 11) and #89 (September 14). Do not substitute non-Nightly builds.
- These missing records are an evidence gap, not proof of a particular retention policy. Full job logs and artifacts are not archived here; the JSON preserves the relevant evidence extracts and stage snapshots.

Coverage sources: [Nightly #85](https://jk3.starbreeze.com/job/payday3/job/trunk/job/XB%20-%20Triggers/job/Nightly/85/), [FullBuild #254](https://jk3.starbreeze.com/job/payday3/job/trunk/job/FullBuild/254/), [Nightly #86](https://jk3.starbreeze.com/job/payday3/job/trunk/job/XB%20-%20Triggers/job/Nightly/86/), [FullBuild #257](https://jk3.starbreeze.com/job/payday3/job/trunk/job/FullBuild/257/).

## Measurements

All displayed durations are minutes, rounded to two decimal places. Exact source values and selected evidence are in [builds.json](builds.json).

| Date | Build | Primary executor | Actions | Executor | Editor stage | Tools stage | Job total |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-09-10 | [791](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/791/) | XGE | 4832 | 11.51 | 14.66 | 11.27 | 115.53 |
| 2026-09-11 | [808](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/808/) | XGE | 4832 | 11.61 | 14.56 | 11.27 | 46.42 |
| 2026-09-14 | [820](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/820/) | XGE | 4832 | 10.50 | 13.51 | 11.77 | 46.67 |
| 2026-09-15 | [835](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/835/) | XGE | 4832 | 28.88 | 32.32 | 13.02 | 86.16 |
| 2026-09-16 | [854](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/854/) | XGE | 4836 | 61.76 | 64.65 | 12.27 | 112.96 |
| 2026-09-17 | [871](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/871/) | UBA (precollection) | 4836 | 25.82 | 30.46 | 12.53 | 81.37 |
| 2026-09-18 | [886](https://jk3.starbreeze.com/job/payday3/job/trunk/job/GameEditor/886/) | UBA (precollection) | 4836 | 15.93 | 18.77 | 17.63 | 58.24 |

| Metric | XGE n | XGE median | XGE mean | XGE min–max | UBA precollection n | UBA median / mean | UBA min–max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Primary executor | 5 | 11.61 | 24.85 | 10.50–61.76 | 2 | 20.87 | 15.93–25.82 |
| Compile Game Editor stage | 5 | 14.66 | 27.94 | 13.51–64.65 | 2 | 24.61 | 18.77–30.46 |
| Compile Engine Tools stage | 5 | 11.77 | 11.92 | 11.27–13.02 | 2 | 15.08 | 12.53–17.63 |
| Jenkins job total | 5 | 86.16 | 81.55 | 46.42–115.53 | 2 | 69.81 | 58.24–81.37 |

The prospective UBA cohort is currently empty. The two early UBA observations are insufficient for a conclusion about a UBA penalty, particularly given the wide XGE variation.

## Interpretation and limitations

Use primary-executor elapsed time as the main acceleration metric and the full editor-stage duration as the operational cross-check. Tools-stage and total-job durations are separate metrics; they include different work and must not be presented as pure executor performance. The tools executor has not been classified from its own artifacts.

The editor artifact contains multiple UBT invocations. XGE builds have a primary 4,832–4,836-action XGE invocation followed by a four-action Unreal Build Accelerator invocation. Classify using the main large invocation and retain secondary invocations separately. A UBA string or `-useUBA` parameter alone does not prove primary UBA execution.

Keep all successful observations, including slow builds. XGE #835 and #854 are much slower than the other three; their cause has not been established. #791 spent 78.43 minutes in Pull Changes, explaining why its job total is unsuitable as a direct executor benchmark. UBA #871 records a remote action error 9666 followed by a local retry and a Jenkins node-block warning. The retry contributes to the observed elapsed time and is not silently excluded.

These are observational builds on different revisions, not a controlled A/B test. Changelist, action count, cache state, helper availability, competing load, retries, network conditions and toolchain/configuration changes can affect elapsed time. Helper capacity, effective cache state and competing load are not established by the saved evidence. No inference about CPU efficiency or equal resource allocation is justified.

## Daily collection protocol

1. Read this report and the JSON before updating; preserve existing records and the frozen baseline. Inspect Git status and follow repository instructions. Do not commit or change branches.
2. Query Nightly builds through authenticated Jenkins MCP. Follow every in-window Nightly to FullBuild and its GameEditor child build(s), including retries or failures. The recent-build endpoint returned only 100 builds even when asked for 150: do not mistake that limit for complete date coverage. The parent pipeline can display the child as `Game Editor`.
3. Verify ancestry and actual clean execution in the child console and Clean Code stage. Fetch status, pipeline stages and the archived `compile_editor.log` path found in the console. Do not assume build numbers or artifact sequence numbers remain fixed.
4. Confirm the primary executor from the main invocation and extract its action count and reported elapsed seconds. Preserve each secondary executor duration independently. Record retries and warnings, node, changelist, source URLs and minimal non-secret evidence.
5. Append completed successful UBA observations in the fixed prospective window as `uba_collection`, deduplicated by job and build ID. Record failed, aborted, incomplete, missing, skipped and non-UBA observations separately under an `observations` field with reasons and source links. Never invent timings, classify unavailable logs by date, or include partial builds in successful statistics. Recheck pending builds.
6. Recalculate each metric independently: sample count, median, arithmetic mean, minimum and maximum. Compute percentage differences as `100 * (UBA statistic / XGE statistic - 1)` using unrounded values. Positive means slower. Show both median and mean comparisons; never compare UBA mean with XGE median. Explain gaps and variability rather than asserting causality. Do not remove slow successful samples post hoc.
7. After the September 28 Nightly completes, reconcile the full window and publish the final comparison in this task. Pause the heartbeat using the automation tool. If resuming late, backfill available in-window builds and report unavailable evidence; do not extend the dates silently.

The scheduled task must only read Jenkins and update these benchmark artifacts. It must not trigger builds, change retention, or modify Jenkins/infrastructure. Remain quiet when there is no meaningful change; notify in Spanish about collection failures needing attention, meaningful anomalies, and the final comparison.

## Validation

Initial records were checked against the authenticated Jenkins status, stage and artifact responses. Nightly and clean flags were checked against console evidence; executor identity and durations came from the actual compile log. JSON structure, unique build IDs, stage/summary consistency and statistics were validated locally with the Python standard library. No application tests or builds are needed for this data-only addition. The future collection cannot be validated until scheduled runs execute.
