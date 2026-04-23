"""
location_summary.py
--------------------
Converts a Google Maps Timeline JSON into a daily location dwell summary CSV.

Pipeline:
  1. Parse position + activity records from timeline.json
  2. Drop records where the phone is moving (speed above threshold)
  3. Cluster stationary fixes within a radius using a greedy centroid algorithm
  4. Compute dwell time per cluster per day (gap-filled, capped at MAX_GAP_MINUTES)
  5. Geocode cluster centroids via Google Maps Geocoding API (one call per cluster)
  6. Write location_summary.csv

Usage:
    python location_summary.py timeline.json --api-key YOUR_GOOGLE_API_KEY

    # Dry run (no geocoding, uses coordinates as name):
    python location_summary.py timeline.json

Options:
    --api-key       Google Maps Geocoding API key
    --radius        Clustering radius in meters (default: 100)
    --max-speed     Max speed m/s to be considered stationary (default: 0.5)
    --max-gap       Max minutes between fixes to count as continuous dwell (default: 30)
    --min-dwell     Min minutes a cluster must be visited in a day to be included (default: 10)
"""

import json
import csv
import sys
import math
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict


# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_RADIUS_M    = 100    # meters — adjust to taste (100–300 is reasonable)
DEFAULT_MAX_SPEED   = 0.25    # m/s — anything above this is "moving"
DEFAULT_MAX_GAP_MIN = 30     # minutes — gap larger than this breaks dwell continuity
DEFAULT_MIN_DWELL   = 44     # minutes — clusters with less dwell are filtered out
API_KEY = 'place your own api key here'
FILE_LOCATION = 'download your location data here'

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_latlng(raw: str):
    cleaned = raw.replace("°", "").strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return None, None


def parse_timestamp(ts_str: str):
    """Parse ISO 8601 timestamp with timezone offset to UTC datetime."""
    if ts_str is None:
        return None
    # Python 3.7+ handles offset-aware ISO 8601 via fromisoformat
    # But the '°' stripping may leave trailing Z or offsets like -08:00
    ts_str = ts_str.strip()
    try:
        dt = datetime.fromisoformat(ts_str)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def load_positions(json_path: Path, max_speed: float):
    """
    Returns list of dicts: {timestamp (UTC datetime), lat, lng}
    Filtered to stationary points only.
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ("rawSignals", "locations", "timelineObjects", "records"):
            if key in data:
                records = data[key]
                break
        else:
            records = []
            for v in data.values():
                if isinstance(v, list):
                    records.extend(v)
    else:
        raise ValueError("Unexpected JSON structure.")

    positions = []
    for rec in records:
        if "position" not in rec:
            continue
        p = rec["position"]
        speed = p.get("speedMetersPerSecond")
        # Exclude points where speed is known and above threshold
        if speed is not None and speed > max_speed:
            continue
        lat, lng = parse_latlng(p.get("LatLng", ""))
        if lat is None:
            continue
        ts = parse_timestamp(p.get("timestamp"))
        if ts is None:
            continue
        positions.append({"ts": ts, "lat": lat, "lng": lng})

    positions.sort(key=lambda x: x["ts"])
    print(f"  Loaded {len(positions):,} stationary position fixes")
    return positions


# ── Clustering ────────────────────────────────────────────────────────────────

def haversine_m(lat1, lng1, lat2, lng2):
    """Distance in meters between two lat/lng points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cluster_positions(positions: list, radius_m: float):
    """
    Greedy centroid clustering.
    Each position is assigned to the nearest existing cluster within radius_m.
    If none found, a new cluster is created.
    Returns list of cluster dicts with centroid lat/lng and list of member positions.
    """
    clusters = []  # each: {lat, lng, points: [...]}

    for pos in positions:
        best_cluster = None
        best_dist = float("inf")

        for c in clusters:
            d = haversine_m(pos["lat"], pos["lng"], c["lat"], c["lng"])
            if d < radius_m and d < best_dist:
                best_dist = d
                best_cluster = c

        if best_cluster is None:
            clusters.append({"lat": pos["lat"], "lng": pos["lng"], "points": [pos]})
        else:
            best_cluster["points"].append(pos)
            # Update centroid (running mean)
            n = len(best_cluster["points"])
            best_cluster["lat"] = sum(p["lat"] for p in best_cluster["points"]) / n
            best_cluster["lng"] = sum(p["lng"] for p in best_cluster["points"]) / n

    print(f"  Found {len(clusters):,} unique location clusters (radius={radius_m}m)")
    return clusters


# ── Dwell time calculation ────────────────────────────────────────────────────

def compute_daily_dwell(clusters: list, max_gap_min: int, min_dwell_min: int, local_offset_hours: int = -8):
    """
    For each cluster, compute dwell minutes per local calendar day.
    Gaps between consecutive fixes larger than max_gap_min are capped at max_gap_min
    to avoid inflating dwell during overnight stays where tracking stopped.

    Returns list of {date, cluster_id, lat, lng, dwell_minutes}.
    """
    max_gap = timedelta(minutes=max_gap_min)
    local_offset = timedelta(hours=local_offset_hours)
    rows = []

    for cid, cluster in enumerate(clusters):
        points = sorted(cluster["points"], key=lambda x: x["ts"])
        if len(points) < 2:
            continue

        # Group consecutive points; accumulate dwell by local date
        daily = defaultdict(float)  # date_str -> minutes

        for i in range(1, len(points)):
            prev = points[i - 1]
            curr = points[i]
            gap = curr["ts"] - prev["ts"]

            # Use the midpoint's local date as the "day" this dwell belongs to
            mid_utc = prev["ts"] + gap / 2
            local_day = (mid_utc + local_offset).strftime("%Y-%m-%d")

            dwell = min(gap, max_gap)
            daily[local_day] += dwell.total_seconds() / 60

        for date_str, minutes in daily.items():
            if minutes >= min_dwell_min:
                rows.append({
                    "date":         date_str,
                    "cluster_id":   cid,
                    "lat":          round(cluster["lat"], 7),
                    "lng":          round(cluster["lng"], 7),
                    "dwell_minutes": round(minutes),
                })

    rows.sort(key=lambda x: (x["date"], -x["dwell_minutes"]))
    return rows


# ── Geocoding ─────────────────────────────────────────────────────────────────

def geocode_cluster(lat: float, lng: float, api_key: str):
    """
    Reverse geocode a lat/lng via Google Maps Geocoding API.
    Returns a human-readable name: establishment > point_of_interest > street address.
    """
    url = (
        f"https://maps.googleapis.com/maps/api/geocode/json"
        f"?latlng={lat},{lng}&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"{lat}, {lng}  [geocode error: {e}]"

    if data.get("status") != "OK":
        return f"{lat}, {lng}  [status: {data.get('status')}]"

    results = data.get("results", [])
    if not results:
        return f"{lat}, {lng}"

    # Prefer named places (establishments, POIs) over street addresses
    for preferred_type in ("establishment", "point_of_interest", "premise"):
        for r in results:
            if preferred_type in r.get("types", []):
                return r["formatted_address"]

    # Fall back to first result (usually a street address)
    return results[0]["formatted_address"]


def geocode_all_clusters(clusters: list, api_key: str):
    """Returns dict: cluster_id -> location name."""
    names = {}
    total = len(clusters)
    for cid, cluster in enumerate(clusters):
        name = geocode_cluster(cluster["lat"], cluster["lng"], api_key)
        names[cid] = name
        # Simple progress indicator
        print(f"  Geocoded {cid + 1}/{total}: {name[:60]}")
    return names


# ── Output ────────────────────────────────────────────────────────────────────

def format_duration(minutes: int):
    h = minutes // 60
    m = minutes % 60
    if h > 0 and m > 0:
        return f"{h}h {m}m"
    elif h > 0:
        return f"{h}h"
    else:
        return f"{m}m"


def write_summary(rows: list, cluster_names: dict, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "location", "dwell_hours", "dwell_minutes", "latitude", "longitude"])
        for row in rows:
            cid = row["cluster_id"]
            name = cluster_names.get(cid, f"{row['lat']}, {row['lng']}")
            dwell_min = row["dwell_minutes"]
            dwell_hrs = round(dwell_min / 60, 2)
            writer.writerow([
                row["date"],
                name,
                dwell_hrs,
                dwell_min,
                row["lat"],
                row["lng"],
            ])
    print(f"\n  ✓ {out_path.name}  ({len(rows):,} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Google Timeline → daily location summary")
    parser.add_argument("json_file",            type=str,   default=FILE_LOCATION,      help="Path to timeline.json")
    parser.add_argument("--api-key",            type=str,   default=API_KEY,             help="Google Geocoding API key")
    parser.add_argument("--radius",             type=float, default=DEFAULT_RADIUS_M,    help="Cluster radius in meters")
    parser.add_argument("--max-speed",          type=float, default=DEFAULT_MAX_SPEED,   help="Max stationary speed m/s")
    parser.add_argument("--max-gap",            type=int,   default=DEFAULT_MAX_GAP_MIN, help="Max gap minutes for dwell continuity")
    parser.add_argument("--min-dwell",          type=int,   default=DEFAULT_MIN_DWELL,   help="Min dwell minutes to include a visit")
    parser.add_argument("--utc-offset",         type=int,   default=-8,                  help="Local UTC offset hours (e.g. -8 for PST)")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"File not found: {json_path}")
        sys.exit(1)

    print(f"\n── Parsing {json_path.name} ──────────────────────────────")
    positions = load_positions(json_path, args.max_speed)

    print(f"\n── Clustering (radius={args.radius}m) ────────────────────")
    clusters = cluster_positions(positions, args.radius)

    print(f"\n── Computing daily dwell times {args.min_dwell} ───────────────────────────")
    rows = compute_daily_dwell(clusters, args.max_gap, args.min_dwell, args.utc_offset)
    print(f"  {len(rows):,} location-day rows before geocoding")

    if args.api_key:
        print(f"\n── Geocoding {len(clusters)} cluster centroids ─────────────")
        # Only geocode clusters that appear in our output rows
        active_cids = {r["cluster_id"] for r in rows}
        active_clusters = {cid: clusters[cid] for cid in active_cids}
        cluster_names = {}
        total = len(active_clusters)
        for i, (cid, cluster) in enumerate(active_clusters.items()):
            name = geocode_cluster(cluster["lat"], cluster["lng"], args.api_key)
            cluster_names[cid] = name
            print(f"  [{i+1}/{total}] {name[:70]}")
    else:
        print(f"\n── No API key provided — using coordinates as location names ──")
        cluster_names = {
            cid: f"{clusters[cid]['lat']}, {clusters[cid]['lng']}"
            for cid in range(len(clusters))
        }

    print(f"\n── Writing output ────────────────────────────────────────")
    out_path = json_path.parent / "location_summary.csv"
    write_summary(rows, cluster_names, out_path)

    print("\nTip: In Power BI, set 'date' as Date type.")
    print("     'latitude'/'longitude' columns enable Map visuals.")
    print("     You can rename locations in the CSV before loading.")


if __name__ == "__main__":
    main()
