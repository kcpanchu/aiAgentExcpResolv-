"""Deterministic coding analysis. The model never does the counting, weighting or master-data checks."""
import re
from dataclasses import dataclass
from datetime import date

FIELDS = ("gl", "cost_center", "order", "wbs", "profit_center", "tax_code")


def norm(v) -> str:
    """Compare SAP keys regardless of leading zeros / case ('0000400000' == '400000')."""
    v = str(v or "").strip().upper()
    return (v.lstrip("0") or "0") if v.isdigit() else v


@dataclass(frozen=True)
class CodingKey:
    gl: str = ""
    cost_center: str = ""
    order: str = ""
    wbs: str = ""
    profit_center: str = ""
    tax_code: str = ""

    def as_dict(self) -> dict:
        return {f: getattr(self, f) for f in FIELDS}


def key_of(line: dict) -> CodingKey:
    return CodingKey(**{f: norm(line.get(f)) for f in FIELDS})


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) >= 3}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def history_candidates(invoices: list[dict], today: date, description: str = "", amount: float | None = None,
                       half_life_days: int = 180, max_results: int = 5) -> list[dict]:
    """Rank the codings this vendor has used, weighted by recency.

    `invoices`: [{"doc","year","date":"YYYY-MM-DD","amount","lines":[{gl,cost_center,...,amount,text}]}]
    share = recency-weighted fraction of the vendor's invoices that used this exact coding.
    """
    total_w, stats = 0.0, {}
    for inv in invoices:
        age = max((today - date.fromisoformat(inv["date"])).days, 0)
        w = 0.5 ** (age / half_life_days)
        total_w += w
        for k in {key_of(ln) for ln in inv.get("lines", [])}:
            st = stats.setdefault(k, {"w": 0.0, "docs": [], "last": "", "amounts": [], "texts": []})
            st["w"] += w
            st["docs"].append(f"{inv['doc']}/{inv.get('year', '')}")
            st["last"] = max(st["last"], inv["date"])
            for ln in inv["lines"]:
                if key_of(ln) == k:
                    st["amounts"].append(abs(float(ln.get("amount") or 0)))
                    st["texts"].append(ln.get("text") or "")
    desc, out = _tokens(description), []
    for k, st in stats.items():
        lo, hi = min(st["amounts"]), max(st["amounts"])
        out.append({
            "coding": k.as_dict(),
            "share": round(st["w"] / total_w, 3) if total_w else 0.0,
            "n_invoices": len(st["docs"]),
            "last_used": st["last"],
            "docs": st["docs"][:5],
            "amount_range": [lo, hi],
            "amount_in_range": None if amount is None else lo * 0.75 <= abs(amount) <= hi * 1.25,
            "text_similarity": round(_jaccard(desc, _tokens(" ".join(st["texts"]))), 3) if desc else None,
        })
    out.sort(key=lambda c: (-c["share"], -c["n_invoices"]))
    return out[:max_results]


def match_candidate(cands: list[dict], line: dict) -> dict | None:
    k = key_of(line).as_dict()
    return next((c for c in cands if c["coding"] == k), None)
