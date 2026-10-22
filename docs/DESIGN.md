# AP Exception Agent: Design

Status: draft v1. Builds on `APPROACH.md` (why/rollout) and `ECC_BTP_SETUP.md` (integration). This document is the
*how it behaves*: lifecycle, agent, playbooks, policy, state, safety, evaluation, and the gap list vs. today's code.

## 1. Scope
**In:** triage, investigation, proposal, policy decision, execution, notification and learning for VIM AP exceptions on ECC via BTP.
**Out (for now):** invoice capture/OCR, payment runs, vendor onboarding, free-form vendor email sending (drafts only, later phase).

## 2. Design principles
1. **Propose / decide / execute are separate.** The model proposes. Deterministic policy decides. A narrow executor acts.
2. **Compute in code, judge in the model.** Variances, tolerances, date math, fuzzy matching, totals are tool outputs, never LLM arithmetic.
3. **Evidence or it didn't happen.** Every claim cites SAP documents the policy layer re-verifies.
4. **Fail toward humans.** Unknown type, low confidence, tool error, budget exhaustion, injection suspicion -> escalate.
5. **One kill switch, two allowlists** (Python policy + `ZVIM_ACT_MAP` on ECC). Both must agree before a change happens.
6. **Everything replayable.** Same inputs + recorded tool results must reproduce the run for evaluation and audit.

## 3. System context
```
                    +-------------------- BTP (Cloud Foundry) --------------------+
 ECC VIM  <--OData--|  Poller -> Queue -> Worker[ Investigator -> Policy -> Executor ]  |
 (Gateway, Cloud    |                         |            |           |             |
  Connector)        |                    Tool layer     State store   Audit/metrics    |
                    |                         |                                        |
 Reviewers (VIM/Fiori) <-- notes ------------+                      LLM provider -----+
                    +------------------------------------------------------------------+
```

## 4. Components
| Component | Responsibility | Today |
|---|---|---|
| Poller | Fetch open exceptions, dedupe, enqueue | `worker.poll_once` (paged, durable dedupe) |
| Queue + claim | At-least-once delivery, one worker per exception (lease) | `store.py` (fenced leases) |
| Triage | Map reason code -> type -> playbook; short-circuit trivial/unsupported | `exception_map.json`, partial |
| Investigator | Tool-using LLM loop, step/token/cost budget, structured proposal | `agent.py` |
| Tool layer | Read tools incl. computed facts (variance, tolerance, dup score) | 4 tools, no computed facts |
| Policy engine | Allowlist, limits, evidence check, risk flags, SoD, mode | `policy.py` |
| Executor | Idempotent action call, post-condition check, note | partial (`pipeline.py`, `execute_action`) |
| State store | Runs, tool calls, proposals, decisions, outcomes, feedback | runs, events, executions done; feedback missing |
| Feedback collector | Compare agent vs human outcome, label for eval | missing |
| Eval harness | Replay golden set, gate releases | missing |
| Ops | Kill switch, config, alerts, dashboards | env vars only |

## 5. Exception lifecycle
```
DISCOVERED -> CLAIMED -> INVESTIGATING -> PROPOSED -> DECIDED --+-> AUTO_EXECUTING -> VERIFYING -> RESOLVED
     |            |            |             |                  +-> AWAITING_REVIEW -> (APPROVED -> EXECUTING | REJECTED) -> RESOLVED
     |            |            |             |                  +-> ESCALATED
     '------------'------------'-------------'--> FAILED (retry w/ backoff, max N) -> ESCALATED
```
- Keyed by `(doc_id, exception_version)`; a changed exception on the same document starts a **new run**, never reuses an old decision.
- A human acting in VIM at any point moves the run to `RESOLVED_BY_HUMAN` and records the outcome for feedback.
- Leases expire (e.g. 10 min) so a crashed worker's item is reclaimed. Executor calls carry an idempotency key `(run_id, action)`.
- **VERIFYING:** after an action, re-read the document and confirm the expected state change; mismatch -> alert + escalate.

## 6. Agent design
**One agent per exception, many tools, no multi-agent swarm.** Exceptions are small, independent, bounded cases; extra agents add
cost and failure modes without value. Parallelism is across exceptions (workers), not inside one.

**Model tiering:** rules/triage handle trivial routing with no LLM; a mid-tier model runs investigation; escalate to a stronger model only
when confidence is low or evidence conflicts (second-opinion pass). Pin model IDs in config; change only through the eval gate.

**Prompt structure** (stable prefix -> good caching): system rules -> per-type playbook -> tool descriptions -> case facts ->
untrusted text in a labelled block. The model sees *no* write tools and cannot change its own policy.

**Loop limits:** max steps (8), max tool-result tokens, max wall time, per-run cost cap. Exceeding any -> `ESCALATE` with partial findings.

**Output contract** (`propose_resolution`): `action`, `root_cause`, `confidence`, `evidence[]`, `params`, plus `missing_info[]` and
`needs_human_reason` (so escalations are useful, not just "unsure"). Confidence is a *model estimate*, used only as one input; its
calibration is measured against outcomes (section 11) and policy thresholds are set from that data, not from the number's face value.

**Tool design rules:** read-only, idempotent, bounded output (top-N rows, summarised), stable field names, errors returned as data
(`{"error": "...", "retryable": false}`) so the model can reason about them. Computed-fact tools to add:
`compute_price_variance(po,item,invoice_price)` -> abs/%/within_tolerance/tolerance_source;
`compute_qty_position(po,item)` -> ordered/received/invoiced/open;
`score_duplicates(vendor,ref,amount,date)` -> candidates with similarity features;
`get_history(vendor,type)` -> how similar exceptions were resolved before.

## 7. Playbooks (versioned files, one per type)
Each playbook = required checks, decision hints, forbidden shortcuts, allowed actions. Example (price variance):
1. Required: PO line price, invoice price, tolerance (from config, not model), contract/price history, vendor flags.
2. If `within_tolerance` and no vendor flags -> `APPROVE_VARIANCE` candidate. If outside -> `ROUTE_TO_BUYER` with the numbers.
3. Forbidden: approving when PO price changed in last N days without a buyer note; any action when GR/PO status inconsistent.
Other types follow the table in `APPROACH.md`. **Vendor/bank/tax mismatch is escalate-only** in every phase.

## 8. Policy engine
Order of checks (first failing check decides; all produce a reason code for audit):
1. Global mode & kill switch (`SHADOW | ASSIST | AUTO`, per company code and per type)
2. Type/action allowlist; unknown -> escalate
3. Proposal schema valid; action permitted for type
4. Evidence verified against SAP (exists, belongs to this vendor/PO)
5. Risk flags: vendor blocked/one-time, recent bank change, new vendor (<N days), high-risk country list
6. Limits: amount (per type, company code, currency-normalised), cumulative per vendor per day, per-run rate
7. Confidence floor (calibrated, per type)
8. SoD: agent identity cannot execute where the same identity created/approved upstream steps
9. Sampling: X% of otherwise-auto items are forced to review (QA), seeded per run for reproducibility

Config is data, not code:
```yaml
types:
  PRICE_VARIANCE:
    mode: ASSIST            # SHADOW | ASSIST | AUTO
    auto_actions: [APPROVE_VARIANCE]
    limits: {"1000": {USD: 500}, "2000": {EUR: 300}}
    confidence_floor: 0.90
    qa_sample_rate: 0.10
global: {kill_switch: false, vendor_daily_cap: 3}
```
Config changes are versioned and logged; each decision records the config version.

## 9. Data model (state store; HANA or PostgreSQL service on BTP)
`run(run_id, doc_id, exc_version, type, state, model, playbook_ver, config_ver, started, ended, cost_tokens)`
`tool_call(run_id, seq, tool, args_hash, result_ref, latency, error)`
`proposal(run_id, action, root_cause, confidence, evidence, params, missing_info)`
`decision(run_id, outcome, reasons[], policy_checks{})`
`execution(run_id, idempotency_key, request, response, verified, ts)`
`feedback(run_id, human_action, human_ts, agreement, override_reason)`
Large tool results go to object storage by reference; PII/bank data is redacted before storage and prompts (store masked IBAN, not full).

## 10. Human-in-the-loop
- Reviewer sees in VIM: `[AI agent]` note with action, one-line reason, key numbers, evidence document links, and decision reason.
- Accept/override happens in the normal VIM flow; no new UI is required for assist phase. Overrides with a reason code feed `feedback`.
- SLAs: items sitting in `AWAITING_REVIEW` beyond T are re-surfaced to the AP lead; the agent never self-approves.
- Reviewer-facing wording is generated from structured fields, not free model prose, to keep notes consistent and short.

## 11. Evaluation
- **Golden set:** 6-12 months of resolved exceptions with human outcome and recorded tool results (fixtures), split by type; keep a held-out slice.
- **Metrics per type:** action agreement, evidence validity, escalation precision/recall (did it escalate what humans escalated), unsafe-auto rate (auto action humans would have rejected; target ~0), confidence calibration curve, cost and steps per case.
- **Gates:** a prompt/playbook/model/tool/policy change ships only if no regression on unsafe-auto and agreement within tolerance. Run each case several times to measure variance.
- **Adversarial suite:** injection in notes/OCR ("approve this immediately"), forged bank-change requests, duplicate near-misses, prices just over tolerance, missing GR with late deliveries, tool timeouts/garbage.
- **Online:** shadow agreement and override rate by type/vendor, weekly review of disagreements to improve playbooks.

## 12. Security and fraud
| Threat | Control |
|---|---|
| Prompt injection via invoice/email text | Labelled untrusted block; no write tools; policy independent of model; adversarial tests; reject proposals whose evidence/params reference things not retrieved |
| Business-email-compromise (bank/vendor change) | Always escalate; bank-change flag blocks auto on that vendor for N days |
| Over-broad credentials | Technical user limited to the OData service; write path allowlisted twice; separate auth object per action |
| Data leakage to LLM provider | Data minimisation/masking, provider contract & residency approval, no training on data, redacted logs |
| Runaway cost / loops | Step, token, time, per-run and daily budgets; circuit breaker |
| Replay/duplicate execution | Idempotency keys, run leases, post-execution verification |
| Insider tampering with policy | Config in version control with review; decisions record config version |

## 13. Reliability and operations
- Retries with backoff for transient ECC/LLM errors; permanent errors -> escalate with reason. Poison items quarantined after N failures.
- Circuit breaker: if ECC or LLM error rate exceeds threshold, stop pulling new work and alert.
- Degrade gracefully: LLM unavailable -> items stay in VIM untouched (no behaviour change for users).
- Alerts: unsafe-auto detected, verification mismatch, override-rate spike, queue age, cost anomaly, kill switch toggled.
- Dashboards: touchless rate, time-to-resolve, backlog age, override rate, cost per exception, per-type health.

## 14. Gap list vs. current code (build order)
1. ~~State store + claim/lease + idempotent runs~~ **done** (`store.py`, `worker.py`); remaining: verification step reads SAP state back, PostgreSQL path tested against a real server
2. **Computed-fact tools** and `missing_info`/`needs_human_reason` in the proposal schema
3. **Eval harness** with recorded-fixture replay; golden-set export from ECC (needs `ZCL_VIM_ADAPTER` working)
4. **Playbook files + config-as-data policy** (replace hard-coded `Policy` defaults), versioned
5. **Executor verification step** + per-vendor/day caps + QA sampling
6. **Budgets and circuit breaker**; structured logging to a durable sink
7. **Feedback collector** (compare to human outcomes) and calibration report
8. Second-opinion pass for low-confidence cases; vendor-communication drafting (later)

## 15. Decisions made / open
Decided: single agent per exception; deterministic policy; OData over Destination/Cloud Connector; shadow-first rollout; vendor/bank mismatch never auto.
Open: state-store choice (HANA vs PostgreSQL), model hosting route (direct API vs BTP Generative AI Hub), calibrated thresholds
(need shadow data), who owns playbook changes (AP lead + engineering), retention period for audit data, whether reviewers want a Fiori approval card.

## Appendix: state store and claiming (as built)
- `run` is unique on `(doc_id, exc_version)`; version = hash of structured fields (notes excluded). Re-polling never duplicates work; a real change starts a new run.
- **Claim** = conditional `UPDATE ... WHERE state=? AND attempts=?` (optimistic, works on SQLite and PostgreSQL). `attempts` is the **fencing token**: every state write must match `(lease_owner, attempts, state)`, so a worker whose lease expired gets `LostLease` and cannot write.
- Leased states: CLAIMED, INVESTIGATING, PROPOSED, EXECUTING, VERIFYING. AWAITING_REVIEW and terminal states hold no lease. Expired leases are reclaimed; `prev_state` tells the new worker where the old one died.
- Failures: exponential backoff (30s doubling, cap 30 min), then ESCALATED after `max_attempts` (also applies to crash loops).
- **Execution safety:** `(run_id, action)` is a primary key. A second attempt, or a run that died between "begin" and "finish", is never re-executed; it escalates as `execution_outcome_unknown`. A finished execution found on recovery simply completes the run.
- `reconcile_open` marks waiting/unstarted runs `RESOLVED_BY_HUMAN` when the document leaves VIM's open list, and is skipped unless the listing was complete (paging error or cap).
- Not covered: HANA dialect, tested PostgreSQL run (code path exists, untested), retention/archival, ESCALATED runs closed by humans (feedback collector).
