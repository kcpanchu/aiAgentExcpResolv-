# ECC + BTP setup for the VIM exception agent

```
Agent worker (BTP Cloud Foundry) --Destination svc--> Connectivity proxy --> Cloud Connector --> ECC Gateway
     src/vim_agent/run.py            ECC_VIM_AGENT        (on-prem proxy)                        /sap/opu/odata/sap/ZVIM_AGENT_SRV
                                                                                                   |
                                                                       ZCL_VIM_AGENT_API (read) + ZIF_VIM_ADAPTER (VIM-specific, write)
```

## 1. ECC side
Prerequisite: SAP Gateway available (embedded in NW 7.40+; hub or add-on for older ECC EHPs).

**SEGW project `ZVIM_AGENT`** (OData V2). Properties are PascalCase; the Python client depends on these names.

| Entity set (key) | Properties | Backed by |
|---|---|---|
| `ExceptionSet` (DocId) | DocId, CompanyCode, Vendor, ReasonCode, Amount, Currency, PoNumber, InvoiceRef, Notes, Status | `zif_vim_adapter~get_open_exceptions` (supports `Status eq 'OPEN'`, `$top`) |
| `PurchaseOrderSet` (PoNumber) + nav `Items` | header + Item, Material, Text, Quantity, Uom, NetPrice, PriceUnit, Plant | `get_po` |
| `GoodsReceiptSet` (PoNumber, Item, Doc, Year) | + PostDate, Quantity, Uom; filter `PoNumber` | `get_goods_receipts` |
| `VendorSet` (Vendor) | Name, RiskFlag, RecentBankChange (Edm.Boolean) | `get_vendor` |
| `InvoiceSet` (DocNumber, FiscalYear) | Vendor, InvoiceRef, Amount, Currency, DocDate; filters Vendor, InvoiceRef, Amount (AND) | `find_invoices` |
| `DocumentSet` (DocNumber) | DocNumber, DocType; 404 if not found | `doc_type` |
| `NoteSet` (create only) | DocId, Text | `post_note` |
| `ActionSet` (create only) | DocId, Action, ParamsJson, DryRun(bool), returns Ok, Message | `execute_action` |

Then: register in `/IWFND/MAINT_SERVICE`, activate the ICF node, test in `/IWFND/GW_CLIENT`.

**Technical user** `ZVIM_AGENT` (system/communication type) with a role that has only:
`S_SERVICE` for this OData service, `M_BEST_BSA` (display), display access to vendor/invoice data,
and the VIM authorization the write adapter needs for the allowlisted actions. No dialog logon, no SAP_ALL.
`ActionSet` should reject any action not on its own allowlist even if the agent service is compromised.

**Not yet implementable without your system:** `ZCL_VIM_ADAPTER` (exception list, note, action). Fill in
after inspecting your VIM configuration; the interface is the contract.

## 2. Cloud Connector
Add an access-control entry: virtual host (e.g. `ecc-vim:8000`, HTTP) -> internal ECC host/port, path prefix
`/sap/opu/odata/sap/ZVIM_AGENT_SRV` only. Use HTTP on the virtual side; Cloud Connector handles backend TLS.

## 3. BTP
Create service instances `vim-agent-destination` (destination service) and `vim-agent-connectivity`.
Destination `ECC_VIM_AGENT`: Type HTTP, URL `http://ecc-vim:8000`, ProxyType `OnPremise`, Authentication
`BasicAuthentication` (technical user), additional property `sap-client=<client>`.
If several Cloud Connectors exist, set `SCC_LOCATION_ID`.

Secrets: bind `ANTHROPIC_API_KEY` through a user-provided service or credential store; never in `manifest.yml`.
Check your security team's approval to send invoice data to the model provider, and minimise PII/bank data in prompts.

## 4. Deploy and phases
`cf push` (manifest defaults are the safest: `DRY_RUN=1`, `AUTO_ENABLED=0`, `POST_NOTES=0` = shadow mode).

| Phase | Settings |
|---|---|
| Shadow | defaults; read `AUDIT_PATH` log / compare with human outcomes |
| Assist | `POST_NOTES=1` |
| Auto (allowlisted types) | `AUTO_ENABLED=1`, `DRY_RUN=0` only after shadow agreement targets are met |

## 5. Known gaps / decisions for you
- `ZCL_VIM_ADAPTER` is a stub; `config/exception_map.json` codes are placeholders.
- `seen` set is in memory; add persistence (HANA/PostgreSQL service) so restarts do not reprocess.
- Audit log is a local file; on CF it is ephemeral, so ship it to your logging/audit service.
- GR query ignores reversals (VGABE 2); net them if GR_MISSING decisions depend on exact quantities.
- Python and ABAP here are covered by unit tests only on the Python side (fake transport); ABAP is unactivated.
