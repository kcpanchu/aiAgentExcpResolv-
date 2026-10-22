# VIM Exception Agent: S/4HANA 2025 vs ECC 6.0

The agent core is identical in both (investigate -> propose -> deterministic policy -> execute -> audit).
What changes is the **SAP integration layer, where the agent runs, and how much you can trust the platform**.
That is why `SapClient` is a Protocol: one adapter per landscape.

> Object names below are from memory; verify against your OpenText VIM version and SAP release.

## 1. Side-by-side

| Dimension | ECC 6.0 | S/4HANA 2025 (on-prem) |
|---|---|---|
| Strategic position | Mainstream maintenance ends 2027 (paid extended to 2030); agent investment must be portable | Current release; target platform |
| Read integration | RFC-enabled custom function modules / BAPIs (`BAPI_PO_GETDETAIL`, etc.), IDocs, or Gateway OData you build | Released OData/REST APIs (e.g. `API_PURCHASEORDER_PROCESS_SRV`, `API_SUPPLIERINVOICE_PROCESS_SRV`, `API_MATERIAL_DOCUMENT_SRV`, Business Partner), CDS views |
| Write integration | VIM's own process-option/action function modules via RFC | Released APIs where they exist; VIM actions via RFC/OData wrapper; keep VIM workflow intact |
| Vendor master | `LFA1/LFB1` | Business Partner (CVI); supplier role |
| Goods movements | `MKPF/MSEG` | `MATDOC` (use APIs, not tables) |
| Custom extension | Z-code in-system (BAdIs, Z-FMs) is normal | Clean core: put logic side-by-side on BTP or use ABAP Cloud/key-user extensibility; avoid modifying standard |
| Eventing | Poll VIM DP tables via RFC, or custom change-pointer/IDoc | Business events -> SAP Event Mesh / Advanced Event Mesh; or poll OData |
| User-facing | VIM work item in SAP GUI / VIM Cockpit; add note | Fiori launchpad; agent summary shown as VIM note or a custom Fiori card |
| AI platform | External only (your service + LLM API) | Same, plus SAP AI Core / Generative AI Hub on BTP, Joule integration option |
| Auth | Technical user, RFC authorization objects (S_RFC), SNC | OAuth2/communication arrangements (communication user + scenario) |
| Performance on lookups | Table reads can be slow on classic DB | HANA + CDS makes similar-invoice / duplicate search cheap |
| Risk | Custom FMs you own and must maintain; upgrade debt | Fewer custom hooks; must stay within released-API contracts |
| Time to first value | Faster if RFC access already exists | Faster if released APIs cover your reads |

## 2. ECC approach (RFC-centric)
> Superseded for this project: with BTP in front of ECC we use OData via Destination + Cloud Connector. See `ECC_BTP_SETUP.md`.
```
Agent service ──pyrfc/SNC──> SAP Cloud Connector ──> ECC
   reads : Z_VIM_GET_EXCEPTION, BAPI_PO_GETDETAIL, Z_GET_GR(po), Z_FIND_DUP(vendor, ref, amt)
   writes: Z_VIM_EXEC_ACTION (thin wrapper over standard VIM action; checks auth + logs)
```
1. Build ~5 small read FMs and **one** write FM. Keep them thin; the policy lives in the agent service, not ABAP.
2. Write FM must call VIM's standard action so workflow, status and history stay correct.
3. Technical user with a role limited to those FMs (S_RFC) and the VIM object auth for the allowed actions only.
4. Poll DP exception items (status = open, type in scope) every N minutes; dedupe by DOCID + status version.
5. Put the agent summary in a VIM note so clerks see it in the existing screen.
6. Design every FM signature to match the OData you will expose later, so the migration is a swap of adapter, not of agent.

## 3. S/4HANA 2025 approach (API + events, clean core)
```
S/4 ──events──> Event Mesh ──> Agent service (BTP or cloud)
Agent ──OAuth2/communication arrangement──> released APIs (read)
Agent ──> VIM action API/wrapper (write, behind policy)
```
1. Prefer released APIs and CDS-based read services; add a small custom OData/RAP service only for VIM-specific data.
2. Subscribe to events (or poll) for new exceptions; no custom polling code in S/4.
3. Run the agent on BTP (CAP/Cloud Foundry/Kyma) for destination, XSUAA/IAS auth and Cloud Connector reuse, or in your own cloud with API Management in front.
4. Optionally use Generative AI Hub on BTP to host/route models inside your SAP contract and data residency boundary.
5. Surface proposals in Fiori (custom card or VIM note) with Approve / Reject buttons that call the executor.
6. Check which VIM version supports your exact S/4 2025 release before committing; VIM compatibility drives timeline.

## 4. Recommended choice
- **Already on ECC and S/4 migration > 18 months away:** do ECC adapter now, scoped to 2-3 exception types; keep SAP-specific code only in `sap_client` implementations.
- **Mid-migration:** build the agent core + policy + eval harness once; implement ECC adapter first, S/4 adapter during migration, run both in parallel.
- **On S/4 already:** go API/event-first on BTP; skip the ECC adapter entirely.

## 5. What does NOT change
Policy engine, evidence verification, shadow -> assist -> auto rollout, audit log, evaluation suite,
untrusted-input handling, kill switch. Reuse the historical exception export as the eval set across both.

## 6. Migration-proofing checklist
- [ ] Agent depends only on the `SapClient` Protocol
- [ ] Field names normalised in the adapter (vendor id, PO, doc numbers)
- [ ] Eval suite uses recorded fixtures, not live SAP
- [ ] Policy thresholds in config, per company code
- [ ] Authorization model documented for both stacks
