"""Tests for the ask -> close negotiation calibration and estimate."""

import pytest

import negotiation


def _cal(pairs=None, discounts=None):
    discounts = discounts if discounts is not None else [i * 0.25 for i in range(40)]
    return {"built_at": "2026-10-06", "n_matched": len(discounts),
            "sales_from": "2026-06", "sales_to": "2026-09",
            "pairs": pairs if pairs is not None else [[0.0, d] for d in discounts],
            "discounts": discounts}


class TestMatchSales:
    def _listing(self, lid, price, sqft=1000.0, first="2026-06-02", last="2026-07-10",
                 project="Test Condo"):
        return {"id": lid, "project_name": project, "price": price, "sqft": sqft,
                "first_seen": first, "last_seen": last}

    def _db(self, *xs):
        # A live listing elsewhere sets "today" (the latest poll) to Oct.
        live = self._listing("live", 1, project="Elsewhere", first="2026-10-01",
                             last="2026-10-06")
        return {x["id"]: x for x in (*xs, live)}

    def _prints(self, *extra):
        # 2026-07 sale at $1.94M on a $2.0M ask, plus three 2025 same-size prints
        # at $1,900 psf to benchmark the ask against.
        jul26 = 2026 * 12 + 7
        base = [(2025 * 12 + m, 1000.0, 1900.0, 1_900_000.0) for m in (3, 6, 9)]
        return {"TESTCONDO": base + [(jul26, 1000.0, 1940.0, 1_940_000.0)] + list(extra)}

    def test_vanished_listing_pairs_with_its_sale(self):
        rows = negotiation.match_sales(self._db(self._listing("a", 2_000_000)), self._prints())
        assert len(rows) == 1
        r = rows[0]
        assert r["discount_pct"] == pytest.approx(3.0)
        assert r["discount_psf"] == pytest.approx(60.0)
        # ask $2,000 psf vs $1,900 prior prints; the sale itself is not in its benchmark
        assert r["premium_pct"] == pytest.approx(5.26, abs=0.01)

    def test_a_listing_still_live_is_not_the_one_that_sold(self):
        db = self._db(self._listing("a", 2_000_000, last="2026-10-05"))
        assert negotiation.match_sales(db, self._prints()) == []

    def test_disagreeing_candidates_make_the_print_ambiguous(self):
        db = self._db(self._listing("a", 2_000_000), self._listing("b", 2_100_000))
        assert negotiation.match_sales(db, self._prints()) == []

    def test_a_different_size_is_a_different_unit(self):
        db = self._db(self._listing("a", 2_000_000, sqft=1100.0))
        assert negotiation.match_sales(db, self._prints()) == []

    def test_thin_benchmark_leaves_premium_unknown(self):
        jul26 = 2026 * 12 + 7
        prints = {"TESTCONDO": [(jul26, 1000.0, 1940.0, 1_940_000.0)]}
        rows = negotiation.match_sales(self._db(self._listing("a", 2_000_000)), prints)
        assert rows and rows[0]["premium_pct"] is None


class TestEstimate:
    def test_none_without_a_calibration(self, monkeypatch):
        monkeypatch.setattr(negotiation, "load", lambda: None)
        assert negotiation.estimate(1_000_000, 1000, 0.0) is None

    def test_median_and_top_quarter_close(self):
        e = negotiation.estimate(1_000_000, 1000, 0.0, cal=_cal())
        assert e["expected_discount_pct"] == pytest.approx(4.9, abs=0.1)
        assert e["strong_discount_pct"] == pytest.approx(7.3, abs=0.1)
        assert e["expected_close_price"] == 951_000
        assert e["expected_discount_psf"] == 49

    def test_conditions_on_how_padded_the_ask_is(self):
        # 100 sales at market closing 1% under, 100 padded +10% closing 5% under;
        # each estimate reads the K=80 nearest, all from one side
        pairs = [[0.0, 1.0]] * 100 + [[10.0, 5.0]] * 100
        cal = _cal(pairs=pairs, discounts=[p[1] for p in pairs])
        assert negotiation.estimate(1e6, 1000, 0.5, cal=cal)["expected_discount_pct"] == 1.0
        assert negotiation.estimate(1e6, 1000, 9.0, cal=cal)["expected_discount_pct"] == 5.0
        # no own prints: every matched sale, and it says so
        e = negotiation.estimate(1e6, 1000, None, cal=cal)
        assert "no own-print benchmark" in e["basis"]

    def test_never_predicts_closing_above_ask(self):
        cal = _cal(discounts=[-2.0] * 40)
        assert negotiation.estimate(1e6, 1000, 0.0, cal=cal)["expected_discount_pct"] == 0.0


class TestAssess:
    @pytest.mark.parametrize("max_buy,tier", [
        (1_000_000, "at_ask"),
        (960_000, "typical"),       # 4% cut: 60% of sales got that
        (930_000, "hard"),          # 7% cut: 30% did
        (900_000, "out_of_reach"),  # 10% cut: none did
    ])
    def test_tiers(self, max_buy, tier):
        a = negotiation.assess(1_000_000, 1000, 0.0, max_buy, cal=_cal())
        assert a["tier"] == tier

    def test_reports_the_cut_in_percent_and_psf(self):
        a = negotiation.assess(1_000_000, 1000, 0.0, 960_000, cal=_cal())
        assert a["cut_needed_pct"] == 4.0 and a["cut_needed_psf"] == 40
        assert a["share_of_sales_this_deep_pct"] == 60

    def test_no_walk_away_price_is_just_the_estimate(self):
        a = negotiation.assess(1_000_000, 1000, 0.0, None, cal=_cal())
        assert "tier" not in a and a["expected_close_price"]
