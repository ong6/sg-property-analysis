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
    # Real shape, from data/bed_bands.json. A "4BR" at 1216 sqft clears its own
    # band on tolerance alone (1350 * 0.88 = 1188) while sitting 2.8% off a 3BR
    # median backed by 6x the contracts.
    "palm gardens": {
        "2": {"lo": 950, "hi": 950, "median": 950, "contracts": 35},
        "3": {"lo": 1250, "hi": 1250, "median": 1250, "contracts": 160},
        "4": {"lo": 1350, "hi": 2350, "median": 1450, "contracts": 28},
    },
    # A 1259 sqft "3BR" lands exactly on the 4BR median; its psf reads cheap
    # because the size belongs to a bigger format.
    "d nest": {
        "3": {"lo": 950, "hi": 1250, "median": 950, "contracts": 250},
        "4": {"lo": 1250, "hi": 1450, "median": 1250, "contracts": 63},
    },
    # The false-positive guard: an 800-sqft-wide 2BR band contaminated by mixed
    # stacks would "contain" a genuine 3BR that misses its own ceiling by 2 sqft.
    "wide band project": {
        "2": {"lo": 950, "hi": 1750, "median": 950, "contracts": 81},
        "3": {"lo": 1050, "hi": 1150, "median": 1150, "contracts": 157},
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


class TestContested:
    """The comparative question `ok` cannot answer.

    Containment alone stopped discriminating once TOLERANCE was applied: 74% of
    adjacent bed-count bands in the real file overlap, so nearly any size fits
    something. These are the two listings that passed `ok` cleanly and were
    wrong anyway.
    """

    def test_a_4br_that_is_really_a_3br_is_contested(self, monkeypatch):
        _patch(monkeypatch)
        v = bed_bands.check("Palm Gardens", 4, 1216)
        assert v["verdict"] == "ok"                # containment still passes it
        assert v["contested"]["looks_like"] == 3
        assert v["contested"]["rule"] == "closer fit + deeper evidence"

    def test_a_size_that_belongs_to_a_bigger_format_is_contested(self, monkeypatch):
        _patch(monkeypatch)
        v = bed_bands.check("D'Nest", 3, 1259)
        assert v["verdict"] == "ok"
        assert v["contested"]["looks_like"] == 4
        assert v["contested"]["rule"] == "containment flip"

    def test_a_wider_rival_band_is_not_evidence(self, monkeypatch):
        # Misses its own 3BR ceiling by 2 sqft and lands in a 950-1750 band that
        # contains almost anything. Without the width guard this whole class of
        # genuine 3BR gets called a fake 2BR.
        _patch(monkeypatch)
        v = bed_bands.check("Wide Band Project", 3, 1152)
        assert v["verdict"] == "ok"
        assert "contested" not in v

    def test_a_clean_unit_is_not_contested(self, monkeypatch):
        _patch(monkeypatch)
        assert "contested" not in bed_bands.check("Regentville", 3, 1100)

    def test_contested_never_changes_the_verdict(self, monkeypatch):
        # Callers gate on `mismatch`; a contested size must stay analysable.
        _patch(monkeypatch)
        for args in [("Palm Gardens", 4, 1216), ("D'Nest", 3, 1259)]:
            assert bed_bands.check(*args)["verdict"] == "ok"

    def test_a_thin_rival_band_cannot_contest(self, monkeypatch):
        monkeypatch.setattr(bed_bands, "load", lambda: {"projects": {
            "p": {"3": {"lo": 1000, "hi": 1100, "median": 1050, "contracts": 100},
                  "4": {"lo": 1100, "hi": 1150, "median": 1120, "contracts": 2}}}})
        assert "contested" not in bed_bands.check("P", 3, 1120)


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
