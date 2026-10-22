from dataclasses import dataclass, field
from enum import Enum


class ExceptionType(str, Enum):
    PRICE_VARIANCE = "PRICE_VARIANCE"
    QTY_VARIANCE = "QTY_VARIANCE"
    GR_MISSING = "GR_MISSING"
    DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"
    NO_PO = "NO_PO"
    VENDOR_MISMATCH = "VENDOR_MISMATCH"
    CODING = "CODING"
    OTHER = "OTHER"


class Action(str, Enum):
    APPROVE_VARIANCE = "APPROVE_VARIANCE"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    ROUTE_TO_BUYER = "ROUTE_TO_BUYER"
    ASSIGN_PO = "ASSIGN_PO"
    PARK_AND_RECHECK = "PARK_AND_RECHECK"
    APPLY_CODING = "APPLY_CODING"
    SUGGEST_CODING = "SUGGEST_CODING"
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
    net_amount: float | None = None   # sum of net lines; needed to reconcile coding lines


@dataclass
class Proposal:
    action: Action
    root_cause: str
    confidence: float
    evidence: list[str] = field(default_factory=list)   # SAP doc numbers the agent relied on
    params: dict = field(default_factory=dict)


@dataclass
class ImageResult:
    """One page of the invoice image, already normalised to a model-supported format."""
    page: int
    page_count: int
    media_type: str
    data: bytes
