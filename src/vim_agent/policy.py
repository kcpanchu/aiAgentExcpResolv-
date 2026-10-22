"""Deterministic authority. The LLM never decides what it is allowed to do."""
from dataclasses import dataclass, field

from .models import Action, Decision, ExceptionType, Proposal, VimException
from .sap_client import SapClient


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


def decide(exc: VimException, p: Proposal, policy: Policy, sap: SapClient) -> tuple[Decision, str]:
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
