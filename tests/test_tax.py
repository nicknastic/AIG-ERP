from aig import tax


def row(**kw):
    base = dict(id=1, fx_rate=1, total=0, gst=0, pst=0, category_line="9270", business_pct=100,
                status="confirmed", expense_date="2026-03-01", currency="CAD", gst_number="123456789RT0001")
    base.update(kw)
    return base


def test_registered_excludes_gst_and_claims_itc():
    c = tax.compute(row(total=112.0, gst=5.0, pst=7.0), gst_registered=True)
    assert c.cost_cad == 107.0  # PST stays in the cost
    assert c.deductible_cad == 107.0
    assert c.itc_cad == 5.0


def test_not_registered_gst_is_part_of_cost():
    c = tax.compute(row(total=112.0, gst=5.0, pst=7.0), gst_registered=False)
    assert c.deductible_cad == 112.0
    assert c.itc_cad == 0


def test_meals_limited_to_half_including_itc():
    c = tax.compute(row(total=105.0, gst=5.0, category_line="8523"), gst_registered=True)
    assert c.deductible_cad == 50.0
    assert c.itc_cad == 2.5


def test_business_use_percentage_and_fx():
    c = tax.compute(row(total=100.0, gst=0, fx_rate=1.4, business_pct=60), gst_registered=True)
    assert c.total_cad == 140.0
    assert c.deductible_cad == 84.0


def test_summary_splits_partners_and_separates_capital():
    rows = [
        row(id=1, total=105.0, gst=5.0, category_line="8810"),
        row(id=2, total=2100.0, gst=100.0, category_line="9936"),
        row(id=3, total=50.0, gst=0, category_line="8523", status="review"),
    ]
    s = tax.summarize(rows, {"gst_registered": "yes", "partner_a_pct": "60", "partner_b_pct": "40"}, 2026)
    assert s.total_deductible == 125.0  # 100 office + 25 meals; CCA excluded
    assert s.partner_a == 75.0 and s.partner_b == 50.0
    assert s.capital.deductible_cad == 2000.0
    assert s.total_itc == 105.0
    assert s.needs_review == 1


def test_missing_gst_number_flagged():
    s = tax.summarize([row(total=105.0, gst=5.0, gst_number=None)], {"gst_registered": "yes",
                      "partner_a_pct": "50", "partner_b_pct": "50"}, 2026)
    assert s.missing_gst_number == 1
