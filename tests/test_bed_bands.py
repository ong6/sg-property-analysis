"""Tests for per-project bedroom→size validation.

The case that motivated this: a Waterview 926 sqft unit listed as "3BR" when
URA rental filings, the developer's unit mix and URA transaction buckets all
call that size a 2-bedroom. listings_db's global table allows 850-1800 sqft for
a 3BR, so it sailed through. The check has to be per-project to bite.
"""

import bed_bands
import weekly


BANDS = {"projects": {
    "waterview": {
        "2": {"lo": 850, "hi": 980, "median": 926, "contracts": 140},
        "3": {"lo": 1109, "hi": 1400, "median": 1150, "contracts": 122},
    },
    "regentville": {
        "2": {"lo": 900, "hi": 1000, "median": 950, "contracts": 60},
        "3": {"lo": 1050, "hi": 1150, "median": 1150, "contracts": 157},
    },
    "thin project": {
        "3": {"lo": 1100, "hi": 1200, "median": 1150, "contracts": 3},
    },
}}


def _patch(monkeypatch):
    monkeypatch.setattr(bed_bands, "load", lambda: BANDS)


class TestCheck:
    def test_the_case_this_exists_for(self, monkeypatch):
        _patch(monkeypatch)
        v = bed_bands.check("Waterview", 3, 926)
        assert v["verdict"] == "mismatch"
        assert v["looks_like"] == 2          # a 2BR+study sold as a 3BR

    def test_genuine_unit_passes(self, monkeypatch):
        _patch(monkeypatch)
        assert bed_bands.check("Regentville", 3, 1152)["verdict"] == "ok"

    def test_oversize_is_not_a_mislabel(self, monkeypatch):
        # Penthouses and corner stacks are genuinely bigger. Calling a roomy
        # unit "mislabelled" would be confident nonsense.
        _patch(monkeypatch)
        v = bed_bands.check("Regentville", 3, 1302)
        assert v["verdict"] == "oversize" and "looks_like" not in v

    def test_undersize_matching_nothing_is_not_a_mislabel(self, monkeypatch):
        _patch(monkeypatch)
        assert bed_bands.check("Regentville", 3, 400)["verdict"] == "undersize"

    def test_thin_band_says_unknown_not_mismatch(self, monkeypatch):
        # 3 contracts cannot be allowed to contradict a listing.
        _patch(monkeypatch)
        v = bed_bands.check("Thin Project", 3, 500)
        assert v["verdict"] == "unknown"

    def test_unknown_project_is_unknown(self, monkeypatch):
        _patch(monkeypatch)
        assert bed_bands.check("Never Heard Of It", 3, 900)["verdict"] == "unknown"

    def test_missing_inputs_are_safe(self, monkeypatch):
        _patch(monkeypatch)
        for args in ((None, 3, 900), ("Waterview", None, 900), ("Waterview", 3, None)):
            assert bed_bands.check(*args)["verdict"] == "unknown"

    def test_tolerance_allows_rounding(self, monkeypatch):
        # Rental filings bucket area into 100 sqft ranges; a listing must miss
        # by a clear margin, not by rounding.
        _patch(monkeypatch)
        assert bed_bands.check("Regentville", 3, 1040)["verdict"] == "ok"


class TestSqftParsing:
    def test_range_midpoint(self):
        assert bed_bands._sqft_mid("1,000 to 1,100") == 1050
        assert bed_bands._sqft_mid("990") == 990
        assert bed_bands._sqft_mid("") is None
        assert bed_bands._sqft_mid(None) is None

    def test_normalize(self):
        assert bed_bands.normalize("THE SAIL @ MARINA BAY") == "the sail marina bay"
        assert bed_bands.normalize("Waterview") == "waterview"


class TestGateIntegration:
    def test_mislabelled_listing_is_gated_out(self, monkeypatch):
        monkeypatch.setattr(bed_bands, "load", lambda: BANDS)
        rows = [{"id": "a", "project_name": "Waterview", "district": "D18",
                 "beds": 3, "price": 1_298_000, "psf": 1402.0,
                 "score_1000": 788, "url": "https://pg/a"}]
        db = {"a": {"id": "a", "price": 1_298_000, "sqft": 926.0, "psf": 1402.0,
                    "status": "active", "ingest_flags": []}}
        short, rej = weekly.select_candidates(rows, db)
        assert short == []
        assert "bed count looks wrong" in rej[0]["gate_reason"]
        assert "2BR band" in rej[0]["gate_reason"]

    def test_genuine_listing_still_passes(self, monkeypatch):
        monkeypatch.setattr(bed_bands, "load", lambda: BANDS)
        rows = [{"id": "b", "project_name": "Regentville", "district": "D19",
                 "beds": 3, "price": 1_280_000, "psf": 1111.0,
                 "score_1000": 855, "url": "https://pg/b"}]
        db = {"b": {"id": "b", "price": 1_280_000, "sqft": 1152.0, "psf": 1111.0,
                    "status": "active", "ingest_flags": []}}
        short, _ = weekly.select_candidates(rows, db)
        assert [c["id"] for c in short] == ["b"]

    def test_a_broken_band_file_does_not_stop_the_scan(self, monkeypatch):
        def boom():
            raise OSError("bands gone")
        monkeypatch.setattr(bed_bands, "load", boom)
        assert weekly._bed_mismatch("Waterview", 3, 926) is None
