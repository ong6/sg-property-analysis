"""Tests for detail-page facilities extraction (Phase 1 of the 3-score system).

PropertyGuru's detail JSON has carried facilities under several shapes over the
years. The extractor must handle each, fall back to a recursive scan when the
key is renamed/moved, and return [] (neutral) when nothing is present — never
guess. These run on synthetic payloads; no live scrape needed.
"""

from scrapers.propertyguru import extract_facilities


def test_flat_string_list():
    data = {"facilities": ["Swimming Pool", "Gym", "24h Security"]}
    assert extract_facilities(data) == ["Swimming Pool", "Gym", "24h Security"]


def test_list_of_objects_name_key():
    data = {"facilities": [{"name": "Pool"}, {"name": "Tennis Court"}]}
    assert extract_facilities(data) == ["Pool", "Tennis Court"]


def test_list_of_objects_label_key():
    data = {"amenities": [{"label": "BBQ Pit"}, {"label": "Function Room"}]}
    assert extract_facilities(data) == ["BBQ Pit", "Function Room"]


def test_comma_joined_string():
    data = {"facilities": "Pool, Gym | Playground / Clubhouse"}
    assert extract_facilities(data) == ["Pool", "Gym", "Playground", "Clubhouse"]


def test_nested_project_facilities():
    data = {"project": {"facilities": ["Lap Pool", "Sky Garden"]}}
    assert extract_facilities(data) == ["Lap Pool", "Sky Garden"]


def test_category_group_wrapper():
    # facilities grouped under category objects with an items collection
    data = {"facilities": [
        {"category": "Recreation", "items": ["Pool", "Gym"]},
        {"category": "Security", "items": [{"name": "Guard House"}]},
    ]}
    assert extract_facilities(data) == ["Pool", "Gym", "Guard House"]


def test_recursive_fallback_renamed_key():
    # PG renames the field — known paths miss it, recursive scan catches it
    data = {"pageProps": {"unitFacilityList": ["Infinity Pool", "Steam Room"]}}
    assert extract_facilities(data) == ["Infinity Pool", "Steam Room"]


def test_dedupe_case_insensitive():
    data = {"facilities": ["Pool", "pool", "POOL", "Gym"]}
    assert extract_facilities(data) == ["Pool", "Gym"]


def test_absent_is_empty():
    assert extract_facilities({"price": 2_000_000, "beds": 3}) == []


def test_non_dict_is_empty():
    assert extract_facilities(None) == []
    assert extract_facilities("Pool, Gym") == []


def test_boolean_facilities_flag_ignored():
    # a hasFacilities:true flag must not crash or produce junk
    assert extract_facilities({"hasFacilities": True, "facilitiesCount": 5}) == []
