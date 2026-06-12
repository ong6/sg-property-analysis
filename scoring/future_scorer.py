"""Future potential scorer for property investment analysis.

Scores a property's future upside potential based on:
- Proximity to upcoming MRT stations (0-6 pts)
- Government development zone (0-6 pts)
- District transformation potential (0-4 pts)
- Supply constraint (0-4 pts)

Total: 20 points max
"""

import json
import math
import os
from datetime import datetime
from typing import Optional

from scoring.models import FutureScore
from utils.geo import normalize_district

# Data directory path
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# v3.4: a line that opened within this many years still counts as a connectivity
# catalyst. Previously any `status:"operational"` (or past-completion) line was
# dropped, so a unit beside a just-opened TEL station (operational 2025) scored 0
# on "future MRT" and was pushed NEGATIVE — penalized for its own upgrade.
RECENT_OPERATIONAL_YEARS = 2

# Module-level caches (loaded once, shared across all FutureScorer instances)
_infra_cache: Optional[dict] = None
_zones_cache: Optional[dict] = None
_profiles_cache: Optional[dict] = None


class FutureScorer:
    """Scores property's future potential (20 pts max).

    Components:
    - Future MRT Proximity: 0-6 pts (within 1km of upcoming MRT 2025-2030)
    - Govt Development Zone: 0-6 pts (JLD, GSW, PDD, etc.)
    - District Transformation: 0-4 pts (rejuvenation plans)
    - Supply Constraint: 0-4 pts (limited new launches = price support)
    """

    def __init__(self):
        self.infra_data = self._load_infrastructure_data()
        self.zone_data = self._load_government_zones()
        self.district_profiles = self._load_district_profiles()
        # Pre-compute qualifying future MRT stations (completing by 2030)
        self._qualifying_stations = self._build_qualifying_stations()

    def _load_infrastructure_data(self) -> dict:
        """Load future MRT infrastructure data (module-level cache)."""
        global _infra_cache
        if _infra_cache is None:
            path = os.path.join(_DATA_DIR, "future_infrastructure.json")
            if os.path.exists(path):
                with open(path) as f:
                    _infra_cache = json.load(f)
            else:
                _infra_cache = {"mrt_lines": {}}
        return _infra_cache

    def _load_government_zones(self) -> dict:
        """Load government development zones data (module-level cache)."""
        global _zones_cache
        if _zones_cache is None:
            path = os.path.join(_DATA_DIR, "government_zones.json")
            if os.path.exists(path):
                with open(path) as f:
                    _zones_cache = json.load(f)
            else:
                _zones_cache = {"development_zones": {}}
        return _zones_cache

    def _load_district_profiles(self) -> dict:
        """Load district profiles data (module-level cache)."""
        global _profiles_cache
        if _profiles_cache is None:
            path = os.path.join(_DATA_DIR, "district_profiles.json")
            if os.path.exists(path):
                with open(path) as f:
                    _profiles_cache = json.load(f)
            else:
                _profiles_cache = {"districts": {}}
        return _profiles_cache

    def score(self, listing: dict) -> FutureScore:
        """Score a property's future potential.

        Args:
            listing: Property listing dictionary with lat, lng, district

        Returns:
            FutureScore with breakdown
        """
        result = FutureScore()

        # A. Future MRT Proximity (0-6 pts)
        mrt_score, mrt_details = self._score_future_mrt(listing)
        result.future_mrt_score = mrt_score
        result.nearest_future_mrt = mrt_details.get("station")
        result.future_mrt_distance_m = mrt_details.get("distance_m")
        result.future_mrt_line = mrt_details.get("line")

        # B. Government Development Zone (0-6 pts)
        zone_score, zones = self._score_govt_zone(listing)
        result.govt_zone_score = zone_score
        result.govt_zones = zones

        # C. District Transformation (0-4 pts)
        result.transformation_score = self._score_transformation(listing)

        # D. Supply Constraint (0-4 pts)
        result.supply_score = self._score_supply_constraint(listing)

        return result

    def _build_qualifying_stations(self) -> list[dict]:
        """Pre-compute list of future MRT stations completing by 2030.

        Called once at init so we don't re-filter per listing.
        """
        stations = []
        current_year = datetime.now().year
        mrt_lines = self.infra_data.get("mrt_lines", {})

        for line_code, line_data in mrt_lines.items():
            completion = line_data.get("completion", "2099")
            try:
                completion_year = int(completion[:4])
            except (ValueError, TypeError):
                completion_year = 2099

            if completion_year > 2030:
                continue
            # v3.4: keep lines opened within the last RECENT_OPERATIONAL_YEARS as a
            # still-relevant connectivity catalyst (don't drop a just-opened line).
            if completion_year < current_year - RECENT_OPERATIONAL_YEARS:
                continue
            # NOTE: status=="operational" is no longer an auto-exclude — completion
            # year is the gate, so a recently-operational line still qualifies.

            line_name = line_data.get("name", line_code)
            for station in line_data.get("stations", []):
                station_lat = station.get("lat")
                station_lng = station.get("lng")
                if station_lat and station_lng:
                    stations.append({
                        "name": station.get("name"),
                        "lat": station_lat,
                        "lng": station_lng,
                        "line": line_name,
                    })

        return stations

    def _score_future_mrt(self, listing: dict) -> tuple[float, dict]:
        """Score based on proximity to upcoming MRT stations (0-6 pts).

        Scoring:
        - Within 500m: 6 pts
        - Within 800m: 4 pts
        - Within 1000m: 2 pts
        - Within 1500m: 1 pt
        """
        lat = listing.get("latitude")
        lng = listing.get("longitude")

        if not lat or not lng:
            return 0, {}

        best_score = 0
        best_station = None
        best_distance = None
        best_line = None

        for station in self._qualifying_stations:
            distance = self._haversine_distance(lat, lng, station["lat"], station["lng"])

            score = 0
            if distance <= 500:
                score = 6
            elif distance <= 800:
                score = 4
            elif distance <= 1000:
                score = 2
            elif distance <= 1500:
                score = 1

            if score > best_score or (score == best_score and distance < (best_distance or float('inf'))):
                best_score = score
                best_station = station["name"]
                best_distance = int(distance)
                best_line = station["line"]

        return best_score, {
            "station": best_station,
            "distance_m": best_distance,
            "line": best_line,
        }

    def _score_govt_zone(self, listing: dict) -> tuple[float, list[str]]:
        """Score based on government development zone (0-6 pts).

        Scoring based on zone priority:
        - High priority zone (JLD, GSW): 6 pts
        - Medium priority zone (PLC, WRC, PDD): 4-5 pts
        - Low priority zone: 2-3 pts
        - Multiple zones: Take highest
        """
        district = normalize_district(listing.get("district", ""))
        if not district:
            return 0, []

        # normalize_district passes non-numeric inputs through uppercased
        # (e.g. "DISTRICT 15"), so int() can raise — guard to neutral.
        try:
            district_num = int(district.replace("D", ""))
        except ValueError:
            return 0, []

        zones_found = []
        best_score = 0

        development_zones = self.zone_data.get("development_zones", {})

        for zone_id, zone_data in development_zones.items():
            zone_districts = zone_data.get("districts", [])
            if district_num in zone_districts:
                zone_name = zone_data.get("short_name", zone_id)
                zones_found.append(zone_name)

                zone_score = zone_data.get("score_points", 0)
                if zone_score > best_score:
                    best_score = zone_score

        return min(best_score, 6), zones_found

    def _score_transformation(self, listing: dict) -> float:
        """Score district transformation potential (0-4 pts).

        Based on:
        - Number of future MRT lines serving the district
        - Number of government zones affecting the district
        - Historical appreciation trend
        """
        district = normalize_district(listing.get("district", ""))
        if not district:
            return 2  # Default moderate score

        try:
            district_num = str(int(district.replace("D", "")))
        except ValueError:
            return 2  # non-numeric district → neutral (same as unknown)
        profile = self.district_profiles.get("districts", {}).get(district_num, {})

        if not profile:
            return 2

        score = 0

        # Count future MRT lines (exclude operational/expired)
        future_mrt = self._filter_future_mrt_lines(profile.get("future_mrt", []))
        if len(future_mrt) >= 2:
            score += 2  # Multiple new lines
        elif len(future_mrt) == 1:
            score += 1

        # Count govt zones
        govt_zones = profile.get("govt_zones", [])
        if len(govt_zones) >= 2:
            score += 2
        elif len(govt_zones) == 1:
            score += 1

        return min(score, 4)

    def _filter_future_mrt_lines(self, lines: list[str]) -> list[str]:
        """Filter MRT lines to only those not yet operational and not past completion."""
        current_year = datetime.now().year
        filtered = []
        for code in lines:
            line_info = self.infra_data.get("mrt_lines", {}).get(code, {})
            completion = line_info.get("completion", "")
            try:
                completion_year = int(str(completion)[:4])
            except (ValueError, TypeError):
                completion_year = None

            # v3.4: recently-operational lines still count (see RECENT_OPERATIONAL_YEARS).
            if completion_year is not None and completion_year < current_year - RECENT_OPERATIONAL_YEARS:
                continue
            filtered.append(code)
        return filtered

    def _score_supply_constraint(self, listing: dict) -> float:
        """Score supply constraint (0-4 pts).

        Limited new supply = price support.
        """
        district = normalize_district(listing.get("district", ""))
        if not district:
            return 2

        try:
            district_num = str(int(district.replace("D", "")))
        except ValueError:
            return 2  # non-numeric district → neutral (same as unknown)
        profile = self.district_profiles.get("districts", {}).get(district_num, {})

        if not profile:
            return 2

        supply_constraint = profile.get("supply_constraint", "medium")

        scores = {
            "very_high": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
        }

        return scores.get(supply_constraint, 2)

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in meters."""
        R = 6371000  # Earth's radius in meters

        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (math.sin(delta_phi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c
