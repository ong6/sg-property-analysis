"""Geographic utilities for distance calculations and district normalization."""

import csv
import math
import os
from dataclasses import dataclass
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_MRT_FILE = os.path.join(_DATA_DIR, "mrt_stations.csv")

_mrt_stations: list["MRTStation"] = []


@dataclass
class MRTStation:
    """MRT station with location data."""
    name: str
    code: str
    lines: str
    is_interchange: bool
    latitude: float
    longitude: float


def _load_mrt_stations() -> list[MRTStation]:
    """Load MRT stations from CSV file."""
    global _mrt_stations
    if _mrt_stations:
        return _mrt_stations

    if not os.path.exists(_MRT_FILE):
        return []

    with open(_MRT_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            station = MRTStation(
                name=row["name"],
                code=row["code"],
                lines=row["lines"],
                is_interchange=row["is_interchange"].lower() == "true",
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
            )
            _mrt_stations.append(station)

    return _mrt_stations


def haversine_distance(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """
    Calculate the great-circle distance between two points on Earth.

    Args:
        lat1, lon1: Latitude and longitude of first point (degrees)
        lat2, lon2: Latitude and longitude of second point (degrees)

    Returns:
        Distance in meters
    """
    # Earth's radius in meters
    R = 6_371_000

    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    # Haversine formula
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c


def find_nearest_mrt(
    latitude: float, longitude: float, max_distance: int = 2000
) -> Optional[tuple[MRTStation, int]]:
    """
    Find the nearest MRT station to a given location.

    Args:
        latitude: Property latitude
        longitude: Property longitude
        max_distance: Maximum distance to search (meters)

    Returns:
        Tuple of (MRTStation, distance_in_meters) or None if none found
    """
    stations = _load_mrt_stations()
    if not stations:
        return None

    nearest = None
    min_distance = float("inf")

    for station in stations:
        dist = haversine_distance(latitude, longitude, station.latitude, station.longitude)
        if dist < min_distance and dist <= max_distance:
            min_distance = dist
            nearest = station

    if nearest:
        return nearest, int(min_distance)
    return None


def find_nearby_mrt_stations(
    latitude: float, longitude: float, max_distance: int = 1000
) -> list[tuple[MRTStation, int]]:
    """
    Find all MRT stations within a given distance.

    Args:
        latitude: Property latitude
        longitude: Property longitude
        max_distance: Maximum distance (meters)

    Returns:
        List of (MRTStation, distance_in_meters) tuples, sorted by distance
    """
    stations = _load_mrt_stations()
    nearby = []

    for station in stations:
        dist = haversine_distance(latitude, longitude, station.latitude, station.longitude)
        if dist <= max_distance:
            nearby.append((station, int(dist)))

    nearby.sort(key=lambda x: x[1])
    return nearby


def get_mrt_distance(listing: dict) -> Optional[tuple[str, int]]:
    """
    Get MRT distance for a listing.

    First tries to use listing's coordinates to calculate distance,
    then falls back to parsing mrt_info string.

    Args:
        listing: Listing dictionary

    Returns:
        Tuple of (station_name, distance_in_meters) or None
    """
    lat = listing.get("latitude")
    lon = listing.get("longitude")

    if lat and lon:
        result = find_nearest_mrt(lat, lon)
        if result:
            station, distance = result
            return station.name, distance

    # Fallback: parse mrt_info string
    mrt_info = listing.get("mrt_info", "")
    if mrt_info:
        import re

        # Extract station name (before dash or parenthesis)
        name_match = re.match(r"^([^-\(]+)", mrt_info)
        station_name = name_match.group(1).strip() if name_match else ""

        # Extract distance
        m_match = re.search(r"(\d+)\s*m(?:eters?)?(?:\s|$|,)", mrt_info.lower())
        if m_match:
            return station_name, int(m_match.group(1))

        # Walking time estimate
        min_match = re.search(r"(\d+)\s*min", mrt_info.lower())
        if min_match:
            return station_name, int(min_match.group(1)) * 80

    return None


def is_near_location(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    max_distance: int = 500,
) -> bool:
    """Check if two points are within a given distance."""
    return haversine_distance(lat1, lon1, lat2, lon2) <= max_distance


# Key locations for tenant pool scoring
KEY_LOCATIONS = {
    "cbd": [
        {"name": "Raffles Place", "lat": 1.2838, "lon": 103.8515},
        {"name": "Marina Bay", "lat": 1.2766, "lon": 103.8546},
        {"name": "Tanjong Pagar", "lat": 1.2765, "lon": 103.8456},
    ],
    "hospitals": [
        {"name": "Singapore General Hospital", "lat": 1.2795, "lon": 103.8355},
        {"name": "National University Hospital", "lat": 1.2935, "lon": 103.7830},
        {"name": "Tan Tock Seng Hospital", "lat": 1.3217, "lon": 103.8467},
        {"name": "Mount Elizabeth Hospital", "lat": 1.3046, "lon": 103.8334},
    ],
    "universities": [
        {"name": "NUS", "lat": 1.2966, "lon": 103.7764},
        {"name": "NTU", "lat": 1.3483, "lon": 103.6831},
        {"name": "SMU", "lat": 1.2973, "lon": 103.8497},
        {"name": "SUTD", "lat": 1.3413, "lon": 103.9635},
    ],
}


def get_tenant_pool_score(latitude: float, longitude: float) -> tuple[int, str]:
    """
    Score tenant pool potential based on proximity to key locations.

    Returns:
        Tuple of (score 0-5, reason)
    """
    # Check CBD proximity
    for cbd in KEY_LOCATIONS["cbd"]:
        dist = haversine_distance(latitude, longitude, cbd["lat"], cbd["lon"])
        if dist <= 3000:
            return 5, f"Near CBD ({cbd['name']})"

    # Check hospital proximity
    for hospital in KEY_LOCATIONS["hospitals"]:
        dist = haversine_distance(latitude, longitude, hospital["lat"], hospital["lon"])
        if dist <= 2000:
            return 4, f"Near hospital ({hospital['name']})"

    # Check university proximity
    for uni in KEY_LOCATIONS["universities"]:
        dist = haversine_distance(latitude, longitude, uni["lat"], uni["lon"])
        if dist <= 2000:
            return 4, f"Near university ({uni['name']})"

    # General RCR/central location bonus
    # Singapore center roughly at 1.29, 103.85
    dist_to_center = haversine_distance(latitude, longitude, 1.29, 103.85)
    if dist_to_center <= 8000:
        return 3, "Central location"

    return 2, "Suburban location"


def normalize_district(district: str) -> str:
    """Normalize district code to D## format.

    Examples: "5" -> "D05", "D5" -> "D05", "D05" -> "D05", "14" -> "D14"
    """
    if not district:
        return ""
    d = str(district).upper().replace("D", "").strip()
    try:
        return f"D{int(d):02d}"
    except ValueError:
        return district.upper()
