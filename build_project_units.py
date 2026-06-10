"""Build data/project_units.json from URA's "No of Dwelling Units" GIS layer.

Source: data.gov.sg dataset d_be71daeab5930f96b90ad2857454d876
("URA No of Dwelling Units", GeoJSON, block-level: PROJ_NAME, PROP_TYPE,
DU = dwelling units per block, X_ADDR/Y_ADDR in SVY21).

Output per non-landed project: total dwelling units (sum of DU across blocks),
block count, and a WGS84 centroid — which fills two long-standing scoring gaps:
  * `dev_size` MMR component: total_units is absent from 6,348/6,349 scraped
    listings (detail-page enrichment is off by default), so the component was
    silently 0 almost everywhere. FullScorer can now fall back to this file.
  * coordinates: listings carry no lat/lng, so the future-MRT proximity
    sub-score and any MRT-distance backtest were blind. The project centroid
    is a good-enough proxy (condo sites are a few hundred meters at most).

SVY21 -> WGS84 is the standard closed-form Transverse Mercator inverse
(no pyproj dependency; verified against OneMap reference points to <1m).

Usage:
  python build_project_units.py /tmp/ura_dwelling_units.geojson
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).parent / "data" / "project_units.json"

# --- SVY21 projection constants (EPSG:3414) ---------------------------------
_A = 6378137.0            # WGS84 semi-major
_F = 1.0 / 298.257223563  # WGS84 flattening
_ORIG_LAT = math.radians(1.366666)     # 1° 22' N
_ORIG_LON = math.radians(103.833333)   # 103° 50' E
_FALSE_N = 38744.572
_FALSE_E = 28001.642
_K0 = 1.0

_B = _A * (1 - _F)
_E2 = (2 * _F) - (_F * _F)
_E4 = _E2 * _E2
_E6 = _E4 * _E2
_A0 = 1 - (_E2 / 4) - (3 * _E4 / 64) - (5 * _E6 / 256)
_A2 = (3.0 / 8) * (_E2 + (_E4 / 4) + (15 * _E6 / 128))
_A4 = (15.0 / 256) * (_E4 + (3 * _E6 / 4))
_A6 = 35 * _E6 / 3072


def _m_of(lat):
    return _A * (_A0 * lat - _A2 * math.sin(2 * lat)
                 + _A4 * math.sin(4 * lat) - _A6 * math.sin(6 * lat))


def svy21_to_wgs84(easting, northing):
    """Closed-form inverse Transverse Mercator (SVY21 -> lat, lng)."""
    n_prime = northing - _FALSE_N
    m_orig = _m_of(_ORIG_LAT)
    m_prime = m_orig + (n_prime / _K0)
    n = (_A - _B) / (_A + _B)
    g = _A * (1 - n) * (1 - n * n) * (1 + (9 * n * n / 4) + (225 * n ** 4 / 64)) * (math.pi / 180)
    sigma = (m_prime * math.pi) / (180.0 * g)
    lat_prime = (sigma
                 + ((3 * n / 2) - (27 * n ** 3 / 32)) * math.sin(2 * sigma)
                 + ((21 * n * n / 16) - (55 * n ** 4 / 32)) * math.sin(4 * sigma)
                 + (151 * n ** 3 / 96) * math.sin(6 * sigma)
                 + (1097 * n ** 4 / 512) * math.sin(8 * sigma))
    sin_lp = math.sin(lat_prime)
    rho = _A * (1 - _E2) / (1 - _E2 * sin_lp ** 2) ** 1.5
    v = _A / math.sqrt(1 - _E2 * sin_lp ** 2)
    psi = v / rho
    t = math.tan(lat_prime)
    e_prime = easting - _FALSE_E
    x = e_prime / (_K0 * v)
    # latitude
    lat = lat_prime - (t / (_K0 * rho)) * (e_prime * x / 2) * (
        1
        - (x * x / 12) * (-4 * psi * psi + 9 * psi * (1 - t * t) + 12 * t * t)
        + (x ** 4 / 360) * (8 * psi ** 4 * (11 - 24 * t * t)
                            - 12 * psi ** 3 * (21 - 71 * t * t)
                            + 15 * psi * psi * (15 - 98 * t * t + 15 * t ** 4)
                            + 180 * psi * (5 * t * t - 3 * t ** 4) + 360 * t ** 4)
    )
    # longitude
    sec_lp = 1 / math.cos(lat_prime)
    lon = _ORIG_LON + sec_lp * (
        x - (x ** 3 / 6) * (psi + 2 * t * t)
        + (x ** 5 / 120) * (-4 * psi ** 3 * (1 - 6 * t * t)
                            + psi * psi * (9 - 68 * t * t)
                            + 72 * psi * t * t + 24 * t ** 4)
    )
    return math.degrees(lat), math.degrees(lon)


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ura_dwelling_units.geojson"
    g = json.load(open(src))
    feats = g.get("features", [])
    print(f"{len(feats):,} block features loaded")

    agg = defaultdict(lambda: {"units": 0, "blocks": 0, "x": 0.0, "y": 0.0,
                               "prop_types": set()})
    for f in feats:
        p = f.get("properties", {})
        name = (p.get("PROJ_NAME") or "").strip()
        if not name or name.upper() in ("NIL", "N.A.", "NA"):
            continue
        du = p.get("DU") or 0
        a = agg[name.upper()]
        a["units"] += int(du)
        a["blocks"] += 1
        a["x"] += float(p.get("X_ADDR") or 0)
        a["y"] += float(p.get("Y_ADDR") or 0)
        a["prop_types"].add(p.get("PROP_TYPE") or "?")

    out = {}
    for name, a in agg.items():
        if a["units"] <= 0 or a["blocks"] == 0:
            continue
        # landed enclaves aren't condo "developments" for dev_size purposes
        non_landed = {t for t in a["prop_types"] if t and t.lower() != "landed"}
        lat, lng = svy21_to_wgs84(a["x"] / a["blocks"], a["y"] / a["blocks"])
        out[name.lower()] = {
            "project_name": name,
            "total_units": a["units"],
            "blocks": a["blocks"],
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "landed_only": not non_landed,
        }

    # The GIS layer merges shared-site developments under one slash-combined
    # PROJ_NAME (e.g. "THE PALETTE/D'NEST"). Emit each part as an alias so
    # listings naming either project still join. units are the COMBINED site
    # total (flagged units_basis) — acceptable for dev_size's saturating tanh.
    aliases = {}
    for key, entry in out.items():
        if "/" not in key:
            continue
        for part in key.split("/"):
            part = part.strip()
            if part and part not in out:
                aliases[part] = entry | {"units_basis": "shared_site"}
    out.update(aliases)

    OUT.write_text(json.dumps({
        "source": "data.gov.sg d_be71daeab5930f96b90ad2857454d876 (URA No of Dwelling Units)",
        "built": __import__("datetime").date.today().isoformat(),
        "projects": out,
    }, indent=1))
    nl = sum(1 for v in out.values() if not v["landed_only"])
    print(f"wrote {len(out):,} projects ({nl:,} non-landed) -> {OUT}")


if __name__ == "__main__":
    main()
