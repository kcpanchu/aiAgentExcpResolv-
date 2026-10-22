# Invoice coding agent: past invoices + image caveats -> coding

Interpretation: "coding" = GL account, cost center, internal order, WBS, profit center, tax code (and amounts per line) for an
incoming invoice that needs coding. "Caveats in images" = anything on the scan that can change coding: handwritten/stamped notes,
cost-center/project/order references, capex or asset wording, service periods, recharge/ship-to departments, tax statements.

## Principle
**Images add caution, never authority.** A scan can send an invoice to a reviewer or corroborate a value history already supports.
It can never be the only basis for automatic coding (scans are forgeable; handwriting is an injection channel).
**History proposes, master data disposes, policy decides.**

## Flow
```
CODING exception (VIM "needs coding")
  -> suggest_coding_from_history     computed in code: recency-weighted share, count, last use, amount range, text similarity
  -> view_invoice_image(page 1..n)   model reads the scan, reports image_findings[]
  -> validate_coding                 SAP master data (account, cost center validity/locks, order, profit center, tax code)
  -> propose APPLY_CODING | SUGGEST_CODING | ESCALATE
  -> policy re-derives everything from SAP (never trusts the model's claims) -> AUTO | REVIEW | ESCALATE
```

## Auto-coding requires ALL of
1. every line passes master-data validation (unsupported checks, e.g. WBS today, fail closed)
2. each line's exact coding appears in the vendor's history: >= `min_invoices` (5) and recency-weighted share >= `min_share` (0.8), used within `max_age_days` (365)
3. lines sum to the invoice **net** amount (`NetAmount` on the exception; unknown net => review)
4. amount <= `max_amount`; no vendor risk / recent bank change
5. every image finding that names a concrete value (`field`+`value`) matches the proposal; any finding with `affects` but no checkable value => review
6. `Policy.auto_enabled` **and** `CodingPolicy.auto_enabled`, and confidence >= auto floor
Everything else is NEEDS_APPROVAL with all reasons listed (so the reviewer sees every issue, not just the first).

## What was built
Python: `coding.py` (history ranking, key normalisation ignoring leading zeros), `images.py` (TIFF/BMP -> PNG via Pillow, 1568px cap),
policy `decide_coding`, agent tools (`get_vendor_coding_history`, `suggest_coding_from_history`, `view_invoice_image`, `validate_coding`) with
images returned as real image blocks, coding playbook prompt, ECC client methods. The vendor/doc/company in tool calls come from the case,
not model arguments, so the model cannot browse other vendors' coding.
ABAP (untested): `get_coding_history`, `validate_coding` in `ZCL_VIM_AGENT_API`; `get_document_image` seam in `ZIF_VIM_ADAPTER`.

## Gateway additions (SEGW, ZVIM_AGENT_SRV)
| Entity set | Key | Properties | Backed by |
|---|---|---|---|
| `VendorCodingSet` | DocNumber, FiscalYear, LineNo | Vendor, CompanyCode, PostingDate (Edm.DateTime), InvoiceRef, Currency, Gl, CostCenter, Order, Wbs, ProfitCenter, TaxCode, Amount, Text; filters Vendor+CompanyCode; **sorted newest first** | `get_coding_history` |
| `CodingCheckSet` | Field, Value | Ok (Edm.Boolean), Reason; filters CompanyCode, PostingDate, Gl, CostCenter, Order, Wbs, ProfitCenter, TaxCode | `validate_coding` |
| `DocumentImageSet` | DocId, Page | PageCount, MimeType; **media entity** (`HasStream`) so `/$value` returns the page | `get_document_image` |
| `ExceptionSet` | + `NetAmount` | add property | adapter |
Add `APPLY_CODING` (and `SUGGEST_CODING` if you want a stored suggestion note) to `ZVIM_ACT_MAP` with a wrapper that applies
`coding_lines` through VIM's standard coding step. The wrapper must ignore `image_findings`, re-validate, and refuse if lines do not reconcile.
Map the placeholder reason code `NO_CODING` in `config/exception_map.json` to your real VIM "coding required" reason.

## Known limits / decide
- History = non-PO FI invoices (`BSIK/BSAK` + `BSEG`). PO-based coding comes from PO account assignment (`EKKN`), not covered yet.
- Document-type filter is "vendor credit item, no special GL"; check against your doc types (credit memos, down payments, parked docs).
- Tax lines are excluded by `KTOSL`; verify for your tax procedure. Tax code *selection* by rate/jurisdiction is not validated, only existence.
- Order status (released/closed) and WBS validation are TODO; both fail closed or are flagged.
- PDF scans are rejected (render to PNG upstream); TIFF needs `pip install pillow` on the worker (`images` extra).
- Vision reading quality is unmeasured: add labelled invoice images (handwriting, stamps, rotated scans) to the eval set before enabling auto.
- Image/PII handling: invoices go to the model provider; confirm approval, and consider masking bank details before sending.
- Multi-line splitting: the agent proposes lines; whether VIM wants percentages or amounts per line depends on your coding step.
