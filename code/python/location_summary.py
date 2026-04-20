"""
location_summary.py
--------------------
Converts a Google Maps Timeline JSON into a daily location dwell summary CSV.

Handles both data sources in the JSON:

  semanticSegments  — Google's pre-processed visits (older data).
                      Visit start/end times are already computed.
                      Uses placeId lookup for business names where available,
                      falls back to reverse geocoding the placeLocation lat/lng.

  rawSignals        — Fine-grained sensor fixes (recent data).
                      Stationary fixes are clustered, moving fixes serve as
                      visit terminators to correctly bound dwell times.
                      Cluster centroids are reverse geocoded.

Both sources produce the same visit schema and are merged before aggregation.

Usage:
    python location_summary.py timeline.json --api-key YOUR_GOOGLE_API_KEY

    # Dry run (no geocoding, coordinates used as names):
    python location_summary.py timeline.json

Options:
    --api-key       Google Maps Geocoding / Places API key
    --radius        rawSignals cluster radius in meters (default: 100)
    --max-speed     Max speed m/s considered stationary in rawSignals (default: 1.5)
    --min-dwell     Min minutes a visit must last to be included (default: 10)
    --utc-offset    Local UTC offset hours, e.g. -8 for PST (default: -8)
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


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_RADIUS_M   = 100
DEFAULT_MAX_SPEED  = 1.5
DEFAULT_MIN_DWELL  = 10
DEFAULT_UTC_OFFSET = -8


# ── Shared helpers ────────────────────────────────────────────────────────────

def parse_latlng(raw: str):
    """
    Handle both clean degrees and the double-encoded UTF-8 seen in the data.
    '37.2970009\u00c2\u00b0, -121.8868181\u00c2\u00b0' → (37.2970009, -121.8868181)
    '37.2961758°, -121.9051795°'                        → (37.2961758, -121.9051795)
    """
    # \u00c2\u00b0 is the double-encoded form of the degree symbol °
    cleaned = raw.replace("\u00c2\u00b0", "").replace("\u00b0", "").replace("°", "").strip()
    parts = [p.strip() for p in cleaned.split(",")]
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            pass
    return None, None


def parse_timestamp(ts_str: str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.strip()).astimezone(timezone.utc)
    except Exception:
        return None


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi    = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Source 1: semanticSegments ────────────────────────────────────────────────

def parse_semantic_visits(segments: list):
    """
    Extract visit records from semanticSegments.
    Only processes segments that have a 'visit' key.
    timelinePath, activity, and timelineMemory segments are ignored.

    Returns list of:
      {start, end, lat, lng, place_id, source='semantic'}
    """
    visits = []
    skipped = 0

    for seg in segments:
        if "visit" not in seg:
            skipped += 1
            continue

        start = parse_timestamp(seg.get("startTime"))
        end   = parse_timestamp(seg.get("endTime"))
        if not start or not end:
            continue

        v       = seg["visit"]
        cand    = v.get("topCandidate", {})
        place_id = cand.get("placeId")

        # placeLocation may be nested under topCandidate
        loc = cand.get("placeLocation", {})
        raw_latlng = loc.get("latLng", "")
        lat, lng = parse_latlng(raw_latlng) if raw_latlng else (None, None)

        visits.append({
            "start":    start,
            "end":      end,
            "lat":      lat,
            "lng":      lng,
            "place_id": place_id,
            "source":   "semantic",
        })

    print(f"  semanticSegments: {len(visits):,} visit segments"
          f"  ({skipped:,} non-visit segments skipped)")
    return visits


# ── Source 2: rawSignals ──────────────────────────────────────────────────────

def load_raw_positions(raw_signals: list, max_speed: float):
    positions = []
    for rec in raw_signals:
        if "position" not in rec:
            continue
        p = rec["position"]
        lat, lng = parse_latlng(p.get("LatLng", ""))
        if lat is None:
            continue
        ts = parse_timestamp(p.get("timestamp"))
        if ts is None:
            continue
        speed = p.get("speedMetersPerSecond")
        is_moving = (speed is not None and speed > max_speed)
        positions.append({"ts": ts, "lat": lat, "lng": lng, "moving": is_moving})

    positions.sort(key=lambda x: x["ts"])
    stationary = sum(1 for p in positions if not p["moving"])
    moving     = len(positions) - stationary
    print(f"  rawSignals: {len(positions):,} fixes  "
          f"({stationary:,} stationary, {moving:,} moving)")
    return positions


def build_clusters(positions: list, radius_m: float):
    clusters = []
    cid_map  = {}

    for i, pos in enumerate(positions):
        if pos["moving"]:
            cid_map[i] = None
            continue

        best_cid, best_dist = None, float("inf")
        for cid, c in enumerate(clusters):
            d = haversine_m(pos["lat"], pos["lng"], c["lat"], c["lng"])
            if d < radius_m and d < best_dist:
                best_dist, best_cid = d, cid

        if best_cid is None:
            best_cid = len(clusters)
            clusters.append({"lat": pos["lat"], "lng": pos["lng"], "count": 1})
        else:
            c = clusters[best_cid]
            c["count"] += 1
            c["lat"] += (pos["lat"] - c["lat"]) / c["count"]
            c["lng"] += (pos["lng"] - c["lng"]) / c["count"]

        cid_map[i] = best_cid

    print(f"  rawSignals: {len(clusters):,} clusters (radius={radius_m}m)")
    return clusters, cid_map


def detect_raw_visits(positions: list, cid_map: dict, clusters: list):
    """
    Walk the full rawSignals timeline. Moving fixes terminate visits correctly.
    Returns list of {start, end, lat, lng, place_id=None, source='raw'}.
    """
    visits = []
    n = len(positions)
    i = 0

    while i < n:
        cid = cid_map.get(i)
        if cid is None:
            i += 1
            continue

        visit_start = positions[i]["ts"]
        j = i + 1
        while j < n and cid_map.get(j) == cid:
            j += 1

        visit_end = positions[j]["ts"] if j < n else positions[j - 1]["ts"]

        visits.append({
            "start":    visit_start,
            "end":      visit_end,
            "lat":      clusters[cid]["lat"],
            "lng":      clusters[cid]["lng"],
            "place_id": None,
            "source":   "raw",
            "cluster_id": cid,
        })
        i = j

    print(f"  rawSignals: {len(visits):,} raw visits detected")
    return visits


# ── Merge & deduplicate ───────────────────────────────────────────────────────

def merge_visits(semantic_visits: list, raw_visits: list):
    """
    Combine both visit lists.
    If a semantic visit overlaps with a raw visit at the same location,
    prefer the semantic one (it has placeId and cleaner times).
    Simple approach: drop raw visits whose time range overlaps a semantic visit.
    """
    if not semantic_visits:
        return raw_visits
    if not raw_visits:
        return semantic_visits

    # Build semantic time windows for overlap checking
    sem_windows = [(v["start"], v["end"]) for v in semantic_visits]

    def overlaps_semantic(raw_v):
        for s, e in sem_windows:
            # Overlap if not (raw ends before sem starts, or raw starts after sem ends)
            if not (raw_v["end"] <= s or raw_v["start"] >= e):
                return True
        return False

    filtered_raw = [v for v in raw_visits if not overlaps_semantic(v)]
    dropped = len(raw_visits) - len(filtered_raw)
    if dropped:
        print(f"  Dedup: dropped {dropped:,} raw visits overlapping semantic visits")

    merged = semantic_visits + filtered_raw
    merged.sort(key=lambda x: x["start"])
    print(f"  Merged total: {len(merged):,} visits")
    return merged


# ── Daily aggregation ─────────────────────────────────────────────────────────

def aggregate_by_day(visits: list, min_dwell_min: int, utc_offset_hours: int):
    """
    Sum visit durations by (lat, lng, place_id, local day).
    Visits crossing midnight are split across both days.
    Returns rows keyed by a location fingerprint for geocoding.
    """
    local_offset = timedelta(hours=utc_offset_hours)

    # daily: (fingerprint, date_str) -> {minutes, lat, lng, place_id}
    daily = defaultdict(lambda: {"minutes": 0.0, "lat": None, "lng": None, "place_id": None})

    for v in visits:
        duration_min = (v["end"] - v["start"]).total_seconds() / 60
        if duration_min <= 0:
            continue

        # Fingerprint: cluster by place_id if available, else by rounded coords
        if v.get("place_id"):
            fingerprint = ("pid", v["place_id"])
        else:
            fp_lat = round(v["lat"] or 0, 4)
            fp_lng = round(v["lng"] or 0, 4)
            fingerprint = ("ll", fp_lat, fp_lng)

        local_start = v["start"] + local_offset
        local_end   = v["end"]   + local_offset

        current = local_start
        while True:
            day_str    = current.strftime("%Y-%m-%d")
            end_of_day = current.replace(hour=23, minute=59, second=59, microsecond=999999)
            seg_end    = min(local_end, end_of_day)
            seg_min    = (seg_end - current).total_seconds() / 60

            if seg_min > 0:
                key = (fingerprint, day_str)
                daily[key]["minutes"]  += seg_min
                daily[key]["lat"]       = v["lat"]
                daily[key]["lng"]       = v["lng"]
                daily[key]["place_id"]  = v.get("place_id")

            if local_end <= end_of_day:
                break
            current = (end_of_day + timedelta(seconds=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

    rows = []
    for (fingerprint, date_str), info in daily.items():
        if info["minutes"] >= min_dwell_min:
            rows.append({
                "date":          date_str,
                "fingerprint":   fingerprint,
                "lat":           info["lat"],
                "lng":           info["lng"],
                "place_id":      info["place_id"],
                "dwell_minutes": round(info["minutes"]),
            })

    rows.sort(key=lambda x: (x["date"], -x["dwell_minutes"]))
    print(f"  {len(rows):,} location-day rows (min_dwell={min_dwell_min}m)")
    return rows


# ── Geocoding ─────────────────────────────────────────────────────────────────

def lookup_place_id(place_id: str, api_key: str):
    """Places API — returns display name for a placeId."""
    url = (
        f"https://maps.googleapis.com/maps/api/place/details/json"
        f"?place_id={urllib.parse.quote(place_id)}"
        f"&fields=name,formatted_address"
        f"&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return None, f"[error: {e}]"

    if data.get("status") != "OK":
        return None, f"[status: {data.get('status')}]"

    result = data.get("result", {})
    name    = result.get("name")
    address = result.get("formatted_address", "")
    return name, address


def reverse_geocode(lat: float, lng: float, api_key: str):
    """Geocoding API — returns best name for a lat/lng."""
    url = (
        f"https://maps.googleapis.com/maps/api/geocode/json"
        f"?latlng={lat},{lng}&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return f"{lat:.5f}, {lng:.5f}  [error: {e}]"

    if data.get("status") != "OK":
        return f"{lat:.5f}, {lng:.5f}  [status: {data.get('status')}]"

    results = data.get("results", [])
    if not results:
        return f"{lat:.5f}, {lng:.5f}"

    for preferred in ("establishment", "point_of_interest", "premise"):
        for r in results:
            if preferred in r.get("types", []):
                return r["formatted_address"]

    return results[0]["formatted_address"]


def resolve_location_names(rows: list, api_key: str):
    """
    Geocode each unique fingerprint once.
    placeId  → Places API (name + address, e.g. "Forma Gym")
    lat/lng  → Geocoding API (address)
    No API key → coordinates as name.

    Returns dict: fingerprint -> display name
    """
    unique = {}
    for row in rows:
        fp = row["fingerprint"]
        if fp not in unique:
            unique[fp] = {"lat": row["lat"], "lng": row["lng"], "place_id": row["place_id"]}

    names  = {}
    total  = len(unique)
    print(f"  Resolving {total} unique locations ...")

    for i, (fp, info) in enumerate(unique.items()):
        if not api_key:
            lat, lng = info["lat"] or 0, info["lng"] or 0
            names[fp] = f"{lat:.5f}, {lng:.5f}"
            continue

        pid = info.get("place_id")
        if pid:
            name, address = lookup_place_id(pid, api_key)
            if name:
                display = f"{name}, {address}" if address else name
            else:
                # placeId lookup failed — fall back to reverse geocode
                lat, lng = info["lat"] or 0, info["lng"] or 0
                display  = reverse_geocode(lat, lng, api_key)
        else:
            lat, lng = info["lat"] or 0, info["lng"] or 0
            display  = reverse_geocode(lat, lng, api_key)

        names[fp] = display
        print(f"  [{i+1}/{total}] {display[:72]}")

    return names


# ── Output ────────────────────────────────────────────────────────────────────

def write_summary(rows: list, location_names: dict, out_path: Path):
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "location", "dwell_hours", "dwell_minutes",
                          "latitude", "longitude", "place_id"])
        for row in rows:
            fp   = row["fingerprint"]
            name = location_names.get(fp, str(fp))
            mins = row["dwell_minutes"]
            hrs  = round(mins / 60, 2)
            writer.writerow([
                row["date"],
                name,
                hrs,
                mins,
                round(row["lat"], 6) if row["lat"] else "",
                round(row["lng"], 6) if row["lng"] else "",
                row["place_id"] or "",
            ])
    print(f"\n  ✓ {out_path.name}  ({len(rows):,} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Google Timeline → daily location dwell summary"
    )
    parser.add_argument("json_file",                                           help="Path to timeline.json")
    parser.add_argument("--api-key",    default=None,                          help="Google Maps API key (Geocoding + Places)")
    parser.add_argument("--radius",     type=float, default=DEFAULT_RADIUS_M,  help=f"rawSignals cluster radius meters (default {DEFAULT_RADIUS_M})")
    parser.add_argument("--max-speed",  type=float, default=DEFAULT_MAX_SPEED, help=f"Max stationary speed m/s (default {DEFAULT_MAX_SPEED})")
    parser.add_argument("--min-dwell",  type=int,   default=DEFAULT_MIN_DWELL, help=f"Min dwell minutes to include (default {DEFAULT_MIN_DWELL})")
    parser.add_argument("--utc-offset", type=int,   default=DEFAULT_UTC_OFFSET,help=f"Local UTC offset hours (default {DEFAULT_UTC_OFFSET})")
    args = parser.parse_args()

    json_path = Path(args.json_file)
    if not json_path.exists():
        print(f"File not found: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Normalise: file may be a bare list (rawSignals only) or the full dict
    if isinstance(data, list):
        raw_signals      = data
        semantic_segs    = []
    else:
        raw_signals      = data.get("rawSignals", [])
        semantic_segs    = data.get("semanticSegments", [])

    all_visits = []

    # ── semanticSegments ──────────────────────────────────────────────────────
    if semantic_segs:
        print(f"\n── semanticSegments ({len(semantic_segs):,} total) ──────────────────")
        sem_visits = parse_semantic_visits(semantic_segs)
        all_visits.extend(sem_visits)

    # ── rawSignals ────────────────────────────────────────────────────────────
    if raw_signals:
        print(f"\n── rawSignals ({len(raw_signals):,} records) ────────────────────────")
        positions          = load_raw_positions(raw_signals, args.max_speed)
        clusters, cid_map  = build_clusters(positions, args.radius)
        raw_visits         = detect_raw_visits(positions, cid_map, clusters)
        all_visits.extend(raw_visits)

    # ── Merge ─────────────────────────────────────────────────────────────────
    print(f"\n── Merging sources ──────────────────────────────────────────────")
    merged = merge_visits(
        [v for v in all_visits if v["source"] == "semantic"],
        [v for v in all_visits if v["source"] == "raw"],
    )

    # ── Aggregate ─────────────────────────────────────────────────────────────
    print(f"\n── Aggregating by day ───────────────────────────────────────────")
    rows = aggregate_by_day(merged, args.min_dwell, args.utc_offset)

    # ── Geocode ───────────────────────────────────────────────────────────────
    print(f"\n── Resolving location names ─────────────────────────────────────")
    location_names = resolve_location_names(rows, args.api_key)

    # ── Write ─────────────────────────────────────────────────────────────────
    print(f"\n── Writing output ───────────────────────────────────────────────")
    out_path = json_path.parent / "location_summary.csv"
    write_summary(rows, location_names, out_path)

    print("\nTips for Power BI:")
    print("  • Set 'date' column type → Date")
    print("  • 'latitude' + 'longitude' enable Map visuals")
    print("  • Rename locations directly in the CSV before importing")
    print("  • PST is -8, PDT is -7 — adjust --utc-offset if data spans DST\n")


if __name__ == "__main__":
    main()
