"""Deterministic authority. The LLM never decides what it is allowed to do."""
from dataclasses import dataclass, field
from datetime import date

from .models import Action, Decision, ExceptionType, Proposal, VimException
from .coding import FIELDS, history_candidates, match_candidate, norm
from .sap_client import SapClient


@dataclass
class CodingPolicy:
    auto_enabled: bool = False        # also requires Policy.auto_enabled
    min_invoices: int = 5             # vendor must have used this exact coding at least this often
    min_share: float = 0.8            # ...in at least this (recency-weighted) share of their invoices
    max_age_days: int = 365           # ...and most recently within this window
    max_amount: float = 2000.0
    history_limit: int = 100


@dataclass
class Policy:
    # exception type -> (actions allowed to auto-execute, max amount)
    auto_rules: dict = field(default_factory=lambda: {
        ExceptionType.PRICE_VARIANCE: ({Action.APPROVE_VARIANCE}, 500.0),
        ExceptionType.GR_MISSING: ({Action.PARK_AND_RECHECK, Action.ROUTE_TO_BUYER}, 1e12),
    })
    never_auto: frozenset = frozenset({ExceptionType.VENDOR_MISMATCH, ExceptionType.OTHER})
    confidence_floor_auto: float = 0.9
    confidence_floor_review: float = 0.5
    auto_enabled: bool = False   # shadow/assist by default; flip per phase
    coding: CodingPolicy = field(default_factory=CodingPolicy)


def decide(exc: VimException, p: Proposal, policy: Policy, sap: SapClient,
           today: date | None = None) -> tuple[Decision, str]:
    if exc.exception_type is ExceptionType.CODING:
        return decide_coding(exc, p, policy, sap, today or date.today())
    if p.action is Action.ESCALATE or exc.exception_type in policy.never_auto:
        return Decision.ESCALATE, "type or action requires human ownership"
    if p.confidence < policy.confidence_floor_review:
        return Decision.ESCALATE, "low confidence"
    # evidence must be real SAP documents, not model inventions
    missing = [e for e in p.evidence if not sap.exists(e)]
    if not p.evidence or missing:
        return Decision.NEEDS_APPROVAL, f"unverified evidence: {missing or 'none cited'}"
    vendor = sap.get_vendor(exc.vendor)
    if vendor.get("risk_flag") or vendor.get("recent_bank_change"):
        return Decision.NEEDS_APPROVAL, "vendor risk flag"
    rule = policy.auto_rules.get(exc.exception_type)
    if (policy.auto_enabled and rule and p.action in rule[0] and exc.amount <= rule[1]
            and p.confidence >= policy.confidence_floor_auto):
        return Decision.AUTO_EXECUTE, "within allowlist and limits"
    return Decision.NEEDS_APPROVAL, "outside auto allowlist or auto disabled"


def _line_amount(line) -> float:
    return float(line["amount"])


def decide_coding(exc: VimException, p: Proposal, policy: Policy, sap: SapClient, today: date) -> tuple[Decision, str]:
    """Everything is re-derived from SAP; nothing the model says about history or validity is trusted.

    Image findings can only add caution (or be corroborated by a matching proposed value); they are never
    a basis for automatic coding. Auto needs: valid master data + strong vendor history + clean reconciliation.
    """
    cp = policy.coding
    if p.action is Action.ESCALATE:
        return Decision.ESCALATE, "agent escalated"
    if p.confidence < policy.confidence_floor_review:
        return Decision.ESCALATE, "low confidence"
    if p.action is Action.SUGGEST_CODING:
        return Decision.NEEDS_APPROVAL, "agent offered a suggestion only"
    if p.action is not Action.APPLY_CODING:
        return Decision.ESCALATE, f"action {p.action.value} is not valid for coding"
    lines = p.params.get("coding_lines")
    try:
        if not isinstance(lines, list) or not lines:
            raise ValueError("no coding lines")
        total = sum(_line_amount(l) for l in lines)
    except (ValueError, KeyError, TypeError) as e:
        return Decision.ESCALATE, f"malformed coding lines: {e}"

    review: list[str] = []
    vendor = sap.get_vendor(exc.vendor)
    if vendor.get("risk_flag") or vendor.get("recent_bank_change"):
        review.append("vendor risk flag")
    if exc.net_amount is None:
        review.append("net amount unknown, cannot reconcile lines")
    elif abs(total - exc.net_amount) > 0.01:
        review.append(f"lines total {total:.2f} != invoice net {exc.net_amount:.2f}")
    if exc.amount > cp.max_amount:
        review.append(f"amount {exc.amount:.2f} above auto limit")

    for i, ln in enumerate(lines, 1):
        res = sap.validate_coding(exc.company_code, ln, today)
        if not res.get("ok"):
            bad = "; ".join(f"{f['field']}={f['value']}: {f['reason']}" for f in res.get("fields", []) if not f.get("ok"))
            review.append(f"line {i} fails master-data validation ({bad or 'no result'})")

    cands = history_candidates(sap.get_vendor_coding_history(exc.vendor, exc.company_code, cp.history_limit), today)
    for i, ln in enumerate(lines, 1):
        c = match_candidate(cands, ln)
        if c is None:
            review.append(f"line {i} coding not found in vendor history")
        elif c["n_invoices"] < cp.min_invoices or c["share"] < cp.min_share:
            review.append(f"line {i} history too weak ({c['n_invoices']} invoices, share {c['share']:.2f})")
        elif (today - date.fromisoformat(c["last_used"])).days > cp.max_age_days:
            review.append(f"line {i} coding not used recently (last {c['last_used']})")

    findings = p.params.get("image_findings", [])
    if not isinstance(findings, list):
        review.append("malformed image findings")
        findings = []
    for f in findings:
        if not isinstance(f, dict):
            review.append("malformed image finding")
            continue
        fld, val = f.get("field"), f.get("value")
        if fld in FIELDS and val:
            if not any(norm(ln.get(fld)) == norm(val) for ln in lines):
                review.append(f"image says {fld}={val} but proposal differs (p.{f.get('page', '?')})")
        elif f.get("affects"):
            review.append(f"image caveat needs a human: {str(f.get('text', ''))[:80]}")

    if review:
        return Decision.NEEDS_APPROVAL, "; ".join(review)
    if policy.auto_enabled and cp.auto_enabled and p.confidence >= policy.confidence_floor_auto:
        return Decision.AUTO_EXECUTE, "coding matches strong vendor history, validated, reconciled"
    return Decision.NEEDS_APPROVAL, "auto disabled or confidence below auto floor"
