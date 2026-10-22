import io
from datetime import date

import pytest

from vim_agent.coding import history_candidates, key_of, match_candidate, norm
from vim_agent.images import UnsupportedImage, normalize_image

TODAY = date(2026, 10, 22)


def inv(doc, d, gl="400000", cc="1000", tax="V1", amt=100.0, text="cloud hosting", extra=None):
    lines = [{"gl": gl, "cost_center": cc, "tax_code": tax, "amount": amt, "text": text}] + (extra or [])
    return {"doc": doc, "year": "2026", "date": d, "lines": lines}


def test_norm_ignores_leading_zeros_and_case():
    assert norm("0000400000") == norm("400000") and norm("ab12") == norm("AB12") and norm("") == ""
    assert key_of({"gl": "0000400000", "cost_center": "0000001000"}) == key_of({"gl": "400000", "cost_center": "1000"})


def test_dominant_coding_has_high_share_and_evidence():
    hist = [inv(f"51{i}", f"2026-0{i}-15") for i in range(1, 7)] + [inv("599", "2026-02-01", gl="610000", cc="2000")]
    c = history_candidates(hist, TODAY, description="Cloud hosting October", amount=110.0)
    assert c[0]["coding"]["gl"] == "400000" and c[0]["n_invoices"] == 6 and c[0]["share"] > 0.8
    assert c[0]["amount_in_range"] is True and c[0]["text_similarity"] > 0.3 and len(c[0]["docs"]) == 5


def test_recent_usage_outweighs_old_usage():
    old = [inv(f"1{i}", "2024-01-10", gl="500000") for i in range(5)]
    new = [inv(f"2{i}", "2026-10-01", gl="400000") for i in range(2)]
    c = history_candidates(old + new, TODAY)
    assert c[0]["coding"]["gl"] == "400000"            # fewer invoices but recent beats stale


def test_split_coding_gives_each_line_credit_once_per_invoice():
    extra = [{"gl": "400000", "cost_center": "1000", "tax_code": "V1", "amount": 5, "text": "x"}]
    c = history_candidates([inv("1", "2026-10-01", extra=extra)], TODAY)
    assert c[0]["n_invoices"] == 1                       # duplicate line in same invoice not double-counted


def test_amount_out_of_range_flagged():
    c = history_candidates([inv("1", "2026-10-01", amt=100)], TODAY, amount=5000)
    assert c[0]["amount_in_range"] is False


def test_match_candidate_requires_exact_coding():
    c = history_candidates([inv("1", "2026-10-01")], TODAY)
    assert match_candidate(c, {"gl": "400000", "cost_center": "1000", "tax_code": "V1"})
    assert match_candidate(c, {"gl": "400000", "cost_center": "2000", "tax_code": "V1"}) is None
    assert match_candidate(c, {"gl": "400000", "cost_center": "1000"}) is None     # missing tax code differs


def test_empty_history():
    assert history_candidates([], TODAY) == []


def test_image_passthrough_and_pdf_rejected():
    assert normalize_image(b"abc", "image/png") == (b"abc", "image/png")
    with pytest.raises(UnsupportedImage):
        normalize_image(b"%PDF", "application/pdf")


def test_tiff_converted_to_png():
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("1", (3000, 2000), 1).save(buf, "TIFF")
    data, mime = normalize_image(buf.getvalue(), "image/tiff")
    out = Image.open(io.BytesIO(data))
    assert mime == "image/png" and max(out.size) <= 1568
