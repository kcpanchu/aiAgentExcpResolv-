from dataclasses import dataclass, field
from enum import Enum


class ExceptionType(str, Enum):
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    GR_MISSING = "GR_MISSING"
    DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"
    NO_PO = "NO_PO"
    VENDOR_MISMATCH = "VENDOR_MISMATCH"
    OTHER = "OTHER"


class Action(str, Enum):
    APPROVE_VARIANCE = "APPROVE_VARIANCE"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    ROUTE_TO_BUYER = "ROUTE_TO_BUYER"
    ASSIGN_PO = "ASSIGN_PO"
    PARK_AND_RECHECK = "PARK_AND_RECHECK"
    ESCALATE = "ESCALATE"


class Decision(str, Enum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    ESCALATE = "ESCALATE"


@dataclass
class VimException:
    doc_id: str          # VIM DP document id
    company_code: str
    vendor: str
    exception_type: ExceptionType
    amount: float
    currency: str
    po_number: str | None = None
    invoice_ref: str | None = None
    notes: str = ""      # untrusted free text (OCR / email)


@dataclass
class Proposal:
    action: Action
    root_cause: str
    confidence: float
    evidence: list[str] = field(default_factory=list)   # SAP doc numbers the agent relied on
    params: dict = field(default_factory=dict)
