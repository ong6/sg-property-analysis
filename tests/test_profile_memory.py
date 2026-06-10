"""Tests for the condo profile store + the listing→stack join."""


import profile_memory as pm


def _isolate(tmp_path, monkeypatch):
    """Point the store at a temp dir so tests don't touch real profiles/."""
    monkeypatch.setattr(pm, "PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(pm, "INDEX_FILE", str(tmp_path / "index.json"))


def test_save_normalizes_and_roundtrips(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    slug = pm.save_profile({"condo": "Test Condo", "district": "D15",
                            "stacks": [{"stack": "01", "beds": 2, "sqft": 900,
                                        "facing": "N", "view": "pool"}]})
    assert slug == "test-condo"
    p = pm.load_profile(slug)
    assert p["slug"] == "test-condo" and p["revision"] == 1
    assert p["researched_at"] and p["first_researched_at"]
    # matcher keys derived at save time
    assert p["stacks"][0]["_size_band"] == "800-1050"
    # re-save bumps revision, preserves first_researched_at
    pm.save_profile({"condo": "Test Condo"})
    assert pm.load_profile(slug)["revision"] == 2


def test_index_rebuilds_from_files(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    pm.save_profile({"condo": "Alpha", "district": "D9", "layouts": [{"type": "2BR", "sqft": [800]}]})
    pm.save_profile({"condo": "Beta", "district": "D10"})
    idx = pm.load_index()["condos"]
    assert set(idx) == {"alpha", "beta"}
    assert idx["alpha"]["layout_count"] == 1


class TestMatchStack:
    PROFILE = {
        "condo": "Match Condo",
        "stacks": [
            {"stack": "01", "beds": 2, "sqft": 900, "facing": "N", "view": "pool",
             "desirability": "high", "flags": []},
            {"stack": "05", "beds": 2, "sqft": 905, "facing": "W", "view": "road",
             "desirability": "low", "flags": ["west_facing", "road_noise"]},
            {"stack": "10", "beds": 3, "sqft": 1100, "facing": "S"},
        ],
        "layouts": [{"type": "1BR", "sqft": [450]}],
    }

    def test_facing_breaks_a_sqft_tie(self):
        # 900 vs 905 are a near-tie on sqft; facing 'W' should pick stack 05.
        m = pm.match_stack({"sqft": 902, "beds": 2, "facing": "West"}, self.PROFILE)
        assert m["stack"] == "05"
        assert "west_facing" in m["flags"]

    def test_beds_filter(self):
        m = pm.match_stack({"sqft": 1100, "beds": 3}, self.PROFILE)
        assert m["stack"] == "10"

    def test_far_sqft_no_match(self):
        assert pm.match_stack({"sqft": 2000, "beds": 2}, self.PROFILE) is None

    def test_layout_fallback_when_no_stacks(self):
        prof = {"condo": "X", "stacks": [], "layouts": [{"type": "1BR", "sqft": [450]}]}
        m = pm.match_stack({"sqft": 452, "beds": 1}, prof)
        assert m["matched"] == "layout" and m["layout_type"] == "1BR"

    def test_no_sqft_returns_none(self):
        assert pm.match_stack({"beds": 2}, self.PROFILE) is None
