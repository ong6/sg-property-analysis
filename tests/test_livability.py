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

    def test_malformed_decimal_returns_none_not_valueerror(self):
        # "[\d.]+" happily matches dotted garbage; float() must not blow up
        assert _mrt_meters("5 min (1.2.3 km) from X") is None
        assert _mrt_meters("(... m)") is None


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

    def test_mid_floor_emits_explicit_zero(self):
        # known-and-neutral is not the same as absent — it must count as a signal
        out = score_livability({"floor_level": "Mid Floor"})
        assert out["components"]["floor"] == 0
        assert out["signals"] == 1

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
        assert near["components"]["mrt"] == 6
        assert far["components"]["mrt"] == -8
        assert score_livability({"mrt_info": "15 min (1.2 km) from X"})["components"]["mrt"] == -4

    def test_typical_bands_score_zero(self):
        # Recentered (audit): present-and-typical must NOT outscore missing —
        # otherwise sorting by Liv partly ranks data completeness.
        assert score_livability({"beds": 2, "sqft": 720})["components"]["space"] == 0
        assert score_livability({"mrt_info": "8 min (650 m) from X"})["components"]["mrt"] == 0
        snug = score_livability({"beds": 2, "sqft": 620})  # ratio 0.86: below typical
        assert -8 < snug["components"]["space"] < 0

    def test_completeness_does_not_outscore_sparse(self):
        # Identical-quality listings, one with typical data present, one bare:
        # both must land on 50 (pre-recenter the complete one scored +7 higher).
        complete = score_livability({"beds": 2, "baths": 1, "sqft": 720,
                                     "mrt_info": "8 min (650 m) from X",
                                     "floor_level": "Mid Floor"})
        sparse = score_livability({"beds": 2, "baths": 1})
        assert complete["score"] == sparse["score"] == 50
        assert complete["signals"] > sparse["signals"]
        assert complete["why"] == "all typical"

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

    def test_ns_facing_bumped(self):
        assert score_livability({"facing": "North"})["components"]["facing"] == 6
        assert score_livability({"facing": "West"})["components"]["facing"] == -5


class TestFacilities:
    def test_full_facilities_scores_high(self):
        out = score_livability({"facilities": [
            "Swimming Pool", "Gym", "24-hour Security", "Tennis Court",
            "BBQ Pit", "Function Room", "Playground"]})
        assert out["components"]["facilities"] == 6

    def test_decent_facilities(self):
        out = score_livability({"facilities": ["Swimming Pool", "Gym", "BBQ Pit"]})
        assert out["components"]["facilities"] == 3

    def test_basic_facilities_neutral(self):
        # 1-2 categories: not a plus, but not penalized either
        out = score_livability({"facilities": ["Swimming Pool", "Covered Car Park"]})
        assert "facilities" not in out["components"]

    def test_absent_facilities_neutral(self):
        assert "facilities" not in score_livability({"beds": 2})["components"]
        assert "facilities" not in score_livability({"facilities": []})["components"]

    def test_full_facilities_raises_score(self):
        base = {"beds": 2, "sqft": 720}
        full = score_livability({**base, "facilities": [
            "Pool", "Gym", "Security Guard", "Tennis", "Sauna"]})
        bare = score_livability(base)
        assert full["score"] > bare["score"]
