# AI Agents for SAP VIM AP Exception Resolution

## 1. Goal
Cut the manual effort and cycle time of VIM (Vendor Invoice Management) exceptions
(price/qty variance, missing PO, GR not posted, duplicate, vendor/tax mismatch, blocked invoice)
without weakening financial controls.

**Principle:** the LLM *reasons and proposes*; deterministic code *decides what is allowed* and *executes*.
The model never holds write credentials and never decides its own authority.

## 2. Architecture

```
 SAP VIM (DP workflow, exception work items)
        |  poll / event (RFC, OData, or BTP Event Mesh)
        v
 [1 Ingest]  -> normalise exception -> queue
        v
 [2 Triage]  -> classify exception type, pick playbook (cheap model / rules first)
        v
 [3 Investigate agent]  READ-ONLY tools: PO, GR, vendor master, invoice history,
        |                 duplicate check, contract/price, prior similar resolutions, attachments (OCR text)
        v
 [4 Proposal]  structured output: root cause, action, confidence, evidence refs
        v
 [5 Policy engine]  deterministic: allowlist, amount limits, confidence floor, SoD, vendor risk
        |--> AUTO_EXECUTE  --> [6 Executor] (service user, narrow auth) --> SAP
        |--> NEEDS_APPROVAL --> human in Fiori / VIM work item with agent's summary
        '--> ESCALATE       --> AP lead / buyer / vendor-comms draft
        v
 [7 Audit + feedback]  every step logged; human overrides become eval data
```

## 3. Exception playbooks (start narrow)
| Exception | Agent investigates | Candidate action | Autonomy target |
|---|---|---|---|
| Price variance within tolerance of contract | PO price, contract, price history | Approve variance / route to buyer | Phase 3 auto if < threshold |
| Qty variance / GR missing | GR docs, delivery notes, PO schedule lines | Ping receiver/buyer, park & recheck | Assist (draft + route) |
| Duplicate suspect | Same vendor/ref/amount/date fuzzy match | Confirm duplicate -> reject; else release | Human approves reject |
| No PO / PO not found | Vendor open POs, amount/line match, email text | Suggest PO assignment | Human approves |
| Vendor/tax/bank mismatch | Vendor master, tax IDs | **Always escalate** (fraud-sensitive) | Never auto |
| Payment block / terms | Block reason, terms on PO vs invoice | Suggest unblock/route | Human approves |

## 4. SAP integration options (pick per landscape)
- **Read:** custom RFC-enabled function modules or CDS/OData services over VIM tables
  (DP header/item/exception data, `/OPT/VIM_*`), plus standard PO/GR/vendor reads (`BAPI_PO_GETDETAIL`, `EKKO/EKPO/EKBE`, `LFA1/LFB1`). Verify exact object names in your release.
- **Write:** use VIM's own process-option/action APIs so workflow, authorizations and logs stay intact; do not write tables directly.
  Use a dedicated technical user with a minimal role, never a dialog user.
- **Transport:** SAP Cloud Connector + BTP (or an API gateway) for on-prem; `pyrfc` or OData from the agent service.
- **Notifications:** agent summary attached as a VIM note/comment so reviewers see it in the existing UI.

## 5. Guardrails (non-negotiable)
1. Separate read and write tool sets; the LLM only sees read tools plus `propose_resolution`.
2. Policy engine in plain code: exception-type allowlist, amount cap per type/company code,
   confidence floor, vendor-risk flags, SoD (agent user cannot both approve and post high-value items).
3. Every proposal must cite evidence (document numbers) that the policy layer re-verifies against SAP.
4. Idempotent executor, dry-run mode, kill switch, rate limits.
5. Treat invoice text/attachments/email as **untrusted input** (prompt-injection risk): never let it alter tools or policy.
6. Full audit trail (inputs, tool calls, proposal, decision, executor result) retained per finance policy; PII/bank data minimised in prompts.
7. Data residency / model-hosting approval from security and compliance.

## 6. Rollout
| Phase | Mode | Exit criteria |
|---|---|---|
| 0 | Data discovery: export 6-12 mo of exceptions + resolutions; rank by volume x effort | Top 5 types identified |
| 1 | **Shadow**: agent proposes, nothing shown | >=85% agreement with human outcome per type |
| 2 | **Assist**: proposal + evidence shown to AP clerk | Handle-time reduction, override rate tracked |
| 3 | **Auto** for allowlisted low-risk types under limits, sampled QA (e.g. 10%) | Zero control findings, error rate < target |
| 4 | Expand types; vendor-communication drafting | |

## 7. Metrics
Touchless rate, mean time to resolve, override rate, post-resolution error/reversal rate,
cost per exception, aging of exception backlog, audit findings.

## 8. Evaluation
Replay historical exceptions as an offline test set (golden outcomes from human resolutions);
gate each prompt/model/playbook change on this suite before release.

## 9. Open questions to confirm
- VIM version, ECC vs S/4HANA, on-prem vs RISE; existing BTP footprint
- Which exception types dominate volume; current tolerances/approval matrix
- Where invoices/e-mails arrive (OpenText Content Server, mailbox) and whether OCR text is available
- Security constraints on sending invoice data to an LLM provider
