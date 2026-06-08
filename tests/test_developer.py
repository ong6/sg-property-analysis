"""Tests for the developer attribution + track-record signal (v3.3 Phase 1)."""

import scoring.developer as dev


def _seed(monkeypatch):
    cache = {
        "updated": "2026-06-08",
        "attribution": {
            "the continuum": {"project_name": "THE CONTINUUM", "district": "D15",
                              "developer": "Hoi Hup Realty & Sunway Developments",
                              "developer_group": "Hoi Hup", "confidence": "high",
                              "source": "https://example.com", "notes": ""},
            "piccadilly grand": {"project_name": "PICCADILLY GRAND", "district": "D08",
                                 "developer": "City Developments Ltd & MCL Land",
                                 "developer_group": "CDL", "confidence": "high",
                                 "source": "https://example.com", "notes": "established"},
            "the myst": {"project_name": "THE MYST", "district": "D23",
                         "developer": "City Developments Limited",
                         "developer_group": "CDL", "confidence": "high",
                         "source": "https://example.com", "notes": ""},
        },
    }
    monkeypatch.setattr(dev, "_cache", cache)


URA = {
    "the continuum": {"transaction_count": 772, "annualized_appreciation": 6.1},
    "piccadilly grand": {"transaction_count": 300, "resale_annualized_appreciation": 5.0},
    "the myst": {"transaction_count": 250, "annualized_appreciation": 3.0},
}


def test_attribution_lookup_is_fuzzy(monkeypatch):
    _seed(monkeypatch)
    assert dev.get_attribution("The Continuum")["developer_group"] == "Hoi Hup"
    # normalized match across punctuation/case
    assert dev.get_attribution("the-continuum")["developer_group"] == "Hoi Hup"
    assert dev.get_attribution("Nonexistent Condo") is None


def test_track_record_groups_jv_by_group(monkeypatch):
    _seed(monkeypatch)
    tr = dev.build_track_record(URA)
    cdl = tr[dev._norm_developer("CDL")]
    # both CDL projects (incl. the JV) group together
    assert cdl["project_count"] == 2
    assert cdl["median_appreciation_pct"] == 4.0  # median(5.0, 3.0)


def test_reference_excludes_self_and_flags_reference_only(monkeypatch):
    _seed(monkeypatch)
    ref = dev.developer_reference("PICCADILLY GRAND", URA)
    assert ref["developer_group"] == "CDL"
    # the subject project is excluded from "also built"; its CDL sibling appears
    names = {p["project"] for p in ref["also_built_in_our_data"]}
    assert "PICCADILLY GRAND" not in names
    assert "THE MYST" in names
    assert "RESEARCH PROMPT" in ref["note"]


def test_unattributed_returns_none(monkeypatch):
    _seed(monkeypatch)
    assert dev.developer_reference("Some Random Condo", URA) is None
