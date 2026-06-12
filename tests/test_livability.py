"""Tests for the livability heuristic — the own-stay axis, separate from MMR."""

from scoring.livability import _mrt_meters, score_livability


class TestMrtParse:
    def test_meters(self):
        assert _mrt_meters("5 min (410 m) from CC12 Bartley MRT Station") == 410

    def test_km(self):
        assert _mrt_meters("15 min (1.2 km) from EW1 Pasir Ris") == 1200

    def test_garbage(self):
        assert _mrt_meters("near MRT") is None
        assert _mrt_meters(None) is None


class TestScore:
    def test_missing_everything_is_neutral(self):
        out = score_livability({})
        assert out["score"] == 50
        assert out["signals"] == 0
        assert out["why"] == "no signals"

    def test_two_bath_beats_one_bath(self):
        base = {"beds": 2, "sqft": 720, "built_year": 2015}
        two = score_livability({**base, "baths": 2})
        one = score_livability({**base, "baths": 1})
        assert two["score"] > one["score"]
        assert two["components"]["baths"] == 10
        assert one["components"]["baths"] == 0  # SG baseline, not penalized

    def test_severely_underbathed_penalized(self):
        out = score_livability({"beds": 3, "baths": 1})
        assert out["components"]["baths"] < 0

    def test_low_floor_penalized_high_rewarded(self):
        low = score_livability({"floor_level": "Low Floor"})
        high = score_livability({"floor_level": "High Floor"})
        assert low["components"]["floor"] < 0 < high["components"]["floor"]

    def test_oversized_is_a_feature_here(self):
        big = score_livability({"beds": 2, "sqft": 950})
        shoebox = score_livability({"beds": 2, "sqft": 520})
        assert big["components"]["space"] > 0 > shoebox["components"]["space"]

    def test_west_facing_penalized(self):
        assert score_livability({"facing": "West"})["components"]["facing"] < 0
        assert score_livability({"facing": "North-South"})["components"]["facing"] > 0

    def test_mrt_distance_tiers(self):
        near = score_livability({"mrt_info": "3 min (250 m) from X"})
        far = score_livability({"mrt_info": "20 min (1.6 km) from X"})
        assert near["components"]["mrt"] > 0 > far["components"]["mrt"]

    def test_score_clamped_0_100(self):
        worst = score_livability({
            "beds": 3, "baths": 1, "sqft": 600, "floor_level": "Ground",
            "facing": "West", "built_year": 1980,
            "mrt_info": "25 min (2.0 km) from X",
        })
        assert 0 <= worst["score"] <= 100

    def test_why_explains_itself(self):
        out = score_livability({"beds": 2, "baths": 2, "sqft": 720})
        assert "baths +10" in out["why"]
