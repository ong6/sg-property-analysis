"""Tests for PropertyGuru detail-page enrichment (floor_level / facing / geo).

Regression cover for the Jul-2026 breakage: the extractor read
`props.pageProps.listingData`, a key PG's detail pages no longer have at all
(the payload moved to `props.pageProps.pageData.data`). The read returned `{}`
on every page, `_extract_detail_page` returned None, and the poller logged
"0 enriched / 0 failed" — which is how floor_level, facing, latitude,
longitude, total_units and developer sat at 0.0% coverage across 9,040
listings, silently disarming the v3.12 floor-aware comparisons that branch on
`listing["floor_level"]`.

Fixtures under tests/fixtures/ are trimmed captures of a REAL detail page
(Affinity at Serangoon, listing 500200706, captured 2026-07-29) — structure and
key names verbatim, payload cut down to the parts the extractor reads. No test
here touches the network.
"""

import json
import os

import config
from scrapers.propertyguru import (
    detail_data_node,
    extract_detail_fields,
    extract_ld_json_fields,
    parse_detail_spec_lines,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


def _next_data():
    return _fixture("pg_detail_next_data.json")


# ---------------------------------------------------------------------------
# Locating the payload inside __NEXT_DATA__
# ---------------------------------------------------------------------------

class TestDetailDataNode:
    def test_finds_current_pagedata_path(self):
        node = detail_data_node(_next_data())
        assert "listingDetail" in node
        assert node["listingDetail"]["id"] == 500200706

    def test_finds_legacy_pageprops_data_path(self):
        legacy = {"props": {"pageProps": {"data": {"listingDetail": {"id": 1}}}}}
        assert detail_data_node(legacy)["listingDetail"]["id"] == 1

    def test_finds_blob_directly_on_pageprops(self):
        legacy = {"props": {"pageProps": {"listingData": {"developer": "X"}}}}
        assert detail_data_node(legacy)["listingData"]["developer"] == "X"

    def test_unknown_shape_is_empty_not_crash(self):
        assert detail_data_node({"props": {"pageProps": {"marketplace": "sg"}}}) == {}
        assert detail_data_node(None) == {}
        assert detail_data_node("not a dict") == {}


# ---------------------------------------------------------------------------
# The structured layer — real payload in, enrichment fields out
# ---------------------------------------------------------------------------

class TestExtractDetailFields:
    def test_real_payload_yields_every_available_field(self):
        fields = extract_detail_fields(detail_data_node(_next_data()))
        assert fields["floor_level"] == "Middle Floor"
        assert fields["furnishing"] == "Unfurnished"
        assert fields["total_units"] == 1052
        assert fields["developer"] == "Oxley Holdings Limited"
        assert fields["latitude"] == 1.36561
        assert fields["longitude"] == 103.87259
        assert "24 hours security" in fields["facilities"]

    def test_floor_level_normalizes_to_a_scorer_tier(self):
        # The whole point of the fix: config's floor-tier logic must be able to
        # read what we store. Before, floor_level was never populated at all.
        fields = extract_detail_fields(detail_data_node(_next_data()))
        assert config.normalize_floor_tier(fields["floor_level"]) == "mid"

    def test_coded_floor_levels_map_to_prose(self):
        # PG ships {"code": ..., "description": ...}; every code must land on a
        # phrase config.normalize_floor_tier understands.
        expected = {"GND": "low", "LOW": "low", "MID": "mid",
                    "HIGH": "high", "PENT": "high"}
        for code, tier in expected.items():
            node = {"listingDetail": {"unitDetails": {"floorLevel": {"code": code}}}}
            value = extract_detail_fields(node)["floor_level"]
            assert config.normalize_floor_tier(value) == tier, code

    def test_description_wins_over_code(self):
        node = {"listingDetail": {"unitDetails": {
            "floorLevel": {"code": "MID", "description": "Middle Floor"}}}}
        assert extract_detail_fields(node)["floor_level"] == "Middle Floor"

    def test_facing_read_from_unit_direction(self):
        node = {"listingDetail": {"unitDetails": {
            "direction": {"code": "NE", "description": "North East"}}}}
        assert extract_detail_fields(node)["facing"] == "North East"

    def test_null_unit_fields_are_omitted_not_guessed(self):
        # Most PG condo listings leave floorLevel/direction null — absent must
        # stay absent so the poller's "only fill blanks" merge writes nothing.
        node = {"listingDetail": {"unitDetails": {"floorLevel": None,
                                                  "direction": None}}}
        fields = extract_detail_fields(node)
        assert "floor_level" not in fields
        assert "facing" not in fields

    def test_lat_lng_fall_back_to_listing_detail_point(self):
        node = {"listingDetail": {"location": {"point": {"lat": 1.3, "lon": 103.8}}}}
        fields = extract_detail_fields(node)
        assert (fields["latitude"], fields["longitude"]) == (1.3, 103.8)

    def test_lat_without_lng_is_dropped(self):
        node = {"listingDetail": {"location": {"point": {"lat": 1.3, "lon": None}}}}
        assert "latitude" not in extract_detail_fields(node)

    def test_developer_falls_back_to_listing_data(self):
        node = {"listingData": {"developer": "Frasers Centrepoint Homes"}}
        assert extract_detail_fields(node)["developer"] == "Frasers Centrepoint Homes"

    def test_legacy_flat_listing_data_still_parses(self):
        # An older/cached flat payload must keep working.
        node = {"latitude": "1.31", "longitude": "103.89", "unitFacing": "South",
                "floorLevel": "High Floor", "furnishing": "Fully Furnished",
                "totalUnits": 320, "developerName": "Some Developer Pte Ltd"}
        fields = extract_detail_fields(node)
        assert fields == {
            "facing": "South", "floor_level": "High Floor",
            "furnishing": "Fully Furnished", "total_units": 320,
            "developer": "Some Developer Pte Ltd",
            "latitude": 1.31, "longitude": 103.89,
        }

    def test_empty_and_garbage_input(self):
        assert extract_detail_fields({}) == {}
        assert extract_detail_fields(None) == {}

    def test_metatable_covers_a_renamed_listing_detail(self):
        # Layer 2: the structured objects vanish, the rendered spec rows in the
        # SAME __NEXT_DATA__ still carry the facts.
        node = detail_data_node(_next_data())
        node.pop("listingDetail")
        node.pop("listingLocationData")
        fields = extract_detail_fields(node)
        assert fields["floor_level"] == "Middle Floor"
        assert fields["total_units"] == 1052
        assert fields["developer"] == "Oxley Holdings Limited"
        assert fields["furnishing"] == "Unfurnished"

    def test_structured_value_wins_over_metatable(self):
        node = detail_data_node(_next_data())
        node["listingDetail"]["unitDetails"]["floorLevel"] = {
            "code": "HIGH", "description": "High Floor"}
        assert extract_detail_fields(node)["floor_level"] == "High Floor"


# ---------------------------------------------------------------------------
# The rendered-spec-row parser (backs both the metatable and the DOM fallback)
# ---------------------------------------------------------------------------

class TestParseDetailSpecLines:
    def test_real_metatable_rows(self):
        rows = ["Condominium for sale", "Unfurnished", "TOP in Dec 2024",
                "99-year lease", "Listed on 27 Jul 2026",
                "Listing ID - 500200706", "732 sqft floor area",
                "S$ 1,885.25 psf (floor)", "Not tenanted",
                "S$ 310.00 monthly maintenance", "Middle floor level",
                "Developed by Oxley Holdings Limited", "1052 total units"]
        assert parse_detail_spec_lines(rows) == {
            "floor_level": "Middle Floor",
            "furnishing": "Unfurnished",
            "developer": "Oxley Holdings Limited",
            "total_units": 1052,
        }

    def test_every_floor_phrase(self):
        cases = {"Ground floor level": "low", "Low floor level": "low",
                 "Middle floor level": "mid", "High floor level": "high",
                 "Penthouse level": "high", "Penthouse": "high"}
        for line, tier in cases.items():
            parsed = parse_detail_spec_lines([line])
            assert config.normalize_floor_tier(parsed["floor_level"]) == tier, line

    def test_thousands_separator_in_total_units(self):
        assert parse_detail_spec_lines(["1,052 total units"])["total_units"] == 1052

    def test_compass_facing_accepted(self):
        assert parse_detail_spec_lines(["North East facing"])["facing"] == "North East"
        assert parse_detail_spec_lines(["Facing: South"])["facing"] == "South"

    def test_non_compass_facing_rejected(self):
        # "Sea facing" / "Pool facing" are marketing copy, not a compass bearing.
        assert "facing" not in parse_detail_spec_lines(["Sea facing"])
        assert "facing" not in parse_detail_spec_lines(["Pool facing"])

    def test_prose_mentioning_floor_level_is_not_a_spec_row(self):
        # Rows are matched whole — a description sentence must not be mined.
        blurb = ("Prices between units may differ due to factors like floor "
                 "level, view, layout, or renovations.")
        assert parse_detail_spec_lines([blurb]) == {}

    def test_noise_and_empty_input(self):
        assert parse_detail_spec_lines([]) == {}
        assert parse_detail_spec_lines(None) == {}
        assert parse_detail_spec_lines(["", "   ", None, 42]) == {}


# ---------------------------------------------------------------------------
# The ld+json layer — PG moved to a single @graph document
# ---------------------------------------------------------------------------

class TestExtractLdJsonFields:
    def test_graph_document_yields_geo(self):
        fields = extract_ld_json_fields(_fixture("pg_detail_ld_json.json"))
        assert fields["latitude"] == 1.36561
        assert fields["longitude"] == 103.87259
        assert fields["furnishing"] == "Unfurnished"

    def test_accepts_a_list_of_documents(self):
        docs = [{"@type": "Organization", "name": "PropertyGuru"},
                {"@type": "Apartment",
                 "geo": {"latitude": "1.30", "longitude": "103.85"}}]
        fields = extract_ld_json_fields(docs)
        assert (fields["latitude"], fields["longitude"]) == (1.30, 103.85)

    def test_additional_property_floor_and_facing(self):
        doc = {"@graph": [{"@type": "Apartment", "additionalProperty": [
            {"name": "Floor Level", "value": "High Floor"},
            {"name": "Facing", "value": "North West"},
        ]}]}
        fields = extract_ld_json_fields(doc)
        assert fields["floor_level"] == "High Floor"
        assert fields["facing"] == "North West"

    def test_no_geo_is_empty(self):
        assert extract_ld_json_fields({"@graph": [{"@type": "Organization"}]}) == {}
        assert extract_ld_json_fields(None) == {}


# ---------------------------------------------------------------------------
# The scraper method wiring — layered, blanks-only merge, None when empty
# ---------------------------------------------------------------------------

class _FakePage:
    """Stands in for a Playwright page: answers the three evaluate() probes."""

    def __init__(self, next_data=None, ld_json=None, body_text="", fail=()):
        self.next_data, self.ld_json, self.body_text = next_data, ld_json, body_text
        self.fail = fail
        self.calls = []

    def evaluate(self, script):
        if "__NEXT_DATA__" in script:
            kind = "next"
        elif "ld+json" in script:
            kind = "ld"
        else:
            kind = "text"
        self.calls.append(kind)
        if kind in self.fail:
            raise RuntimeError(f"evaluate({kind}) blew up")
        return {"next": self.next_data, "ld": self.ld_json,
                "text": self.body_text}[kind]


def _scraper_with(page):
    from scrapers.propertyguru import PropertyGuruScraper
    scraper = PropertyGuruScraper.__new__(PropertyGuruScraper)
    scraper._page = page
    return scraper


class TestExtractDetailPage:
    def test_real_page_populates_the_enrichment_contract(self):
        page = _FakePage(next_data=_next_data(),
                         ld_json=[_fixture("pg_detail_ld_json.json")])
        data = _scraper_with(page)._extract_detail_page()
        assert data["floor_level"] == "Middle Floor"
        assert data["total_units"] == 1052
        assert data["developer"] == "Oxley Holdings Limited"
        assert data["latitude"] == 1.36561
        assert data["facilities"]

    def test_dom_text_is_the_last_resort(self):
        # __NEXT_DATA__ gone entirely (the failure mode that broke this once).
        body = ("Affinity At Serangoon\nS$ 1,380,000\nMiddle floor level\n"
                "Unfurnished\nDeveloped by Oxley Holdings Limited\n"
                "1052 total units\n")
        page = _FakePage(next_data=None, ld_json=[_fixture("pg_detail_ld_json.json")],
                         body_text=body)
        data = _scraper_with(page)._extract_detail_page()
        assert data["floor_level"] == "Middle Floor"
        assert data["total_units"] == 1052
        assert data["developer"] == "Oxley Holdings Limited"
        assert data["latitude"] == 1.36561          # still from ld+json

    def test_dom_layer_skipped_when_json_was_complete(self):
        page = _FakePage(next_data=_next_data(),
                         ld_json=[_fixture("pg_detail_ld_json.json")],
                         body_text="Ground floor level")
        data = _scraper_with(page)._extract_detail_page()
        assert "text" not in page.calls
        assert data["floor_level"] == "Middle Floor"

    def test_earlier_layer_wins_over_later_ones(self):
        page = _FakePage(next_data=_next_data(),
                         ld_json=[{"@type": "Apartment",
                                   "geo": {"latitude": 9.9, "longitude": 9.9}}])
        data = _scraper_with(page)._extract_detail_page()
        assert data["latitude"] == 1.36561

    def test_one_broken_layer_does_not_kill_the_rest(self):
        page = _FakePage(next_data=_next_data(), fail=("ld", "text"))
        data = _scraper_with(page)._extract_detail_page()
        assert data["floor_level"] == "Middle Floor"

    def test_nothing_found_returns_none(self):
        # Contract poller._enrich_one depends on: an empty extraction is None,
        # so the cycle can tell "visited and found nothing" from "found some".
        page = _FakePage(next_data=None, ld_json=[], body_text="Just a moment...")
        assert _scraper_with(page)._extract_detail_page() is None


# ---------------------------------------------------------------------------
# Poller merge: an empty list counts as blank (facilities' model default)
# ---------------------------------------------------------------------------

def test_poller_treats_empty_collections_as_blank():
    import poller
    assert poller._is_blank([]) is True
    assert poller._is_blank(None) is True
    assert poller._is_blank("") is True
    assert poller._is_blank(0) is True
    assert poller._is_blank(["Pool"]) is False
    assert poller._is_blank("High Floor") is False
    assert poller._is_blank(1.36561) is False
