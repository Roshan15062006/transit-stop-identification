"""
fetch_bus_stops.py
==================
Fetch bus stop locations from OpenStreetMap using the Overpass API
and save them to a CSV file.

Part of: Transit Stop Identification (AI/ML Project)

INSTALL REQUIRED LIBRARIES
---------------------------
    pip install requests

RUN
---
    # Fetch stops for Bengaluru (default)
    python fetch_bus_stops.py

    # Choose a different city
    python fetch_bus_stops.py --city mumbai
    python fetch_bus_stops.py --city delhi
    python fetch_bus_stops.py --city london

    # Custom output filename
    python fetch_bus_stops.py --city chennai --output chennai_stops.csv

    # Limit number of results (useful for testing)
    python fetch_bus_stops.py --city bengaluru --limit 50

HOW IT WORKS
------------
1. We send a query to the Overpass API (the read API for OpenStreetMap).
2. The query asks for all nodes tagged "highway=bus_stop" inside a
   bounding box (a rectangle defined by south, west, north, east).
3. The API returns JSON with id, latitude, longitude, and extra tags.
4. We parse the response and save the results to a CSV file.
"""

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


# ──────────────────────────────────────────────────────────────
# CITY BOUNDING BOXES
# Format: (south_lat, west_lon, north_lat, east_lon)
# Find boxes for any city at: https://bboxfinder.com
# ──────────────────────────────────────────────────────────────
CITY_BBOXES = {
    "bengaluru":  (12.834, 77.461, 13.139, 77.784),
    "mumbai":     (18.894, 72.776, 19.270, 72.987),
    "delhi":      (28.405, 76.838, 28.883, 77.346),
    "chennai":    (12.900, 80.099, 13.233, 80.328),
    "hyderabad":  (17.243, 78.270, 17.560, 78.629),
    "kolkata":    (22.452, 88.206, 22.669, 88.475),
    "pune":       (18.421, 73.736, 18.637, 73.984),
    "london":     (51.450, -0.250, 51.570, -0.050),
    "new york":   (40.670, -74.030, 40.820, -73.870),
    "berlin":     (52.338, 13.088, 52.675, 13.761),
}

# Overpass API endpoint (public, free, no key needed)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Backup mirror (use if main server is busy)
OVERPASS_MIRROR = "https://overpass.kumi.systems/api/interpreter"


# ──────────────────────────────────────────────────────────────
# STEP 1 — BUILD THE OVERPASS QUERY
# ──────────────────────────────────────────────────────────────

def build_query(bbox):
    """
    Build an Overpass QL query string for bus stops inside a bounding box.

    Overpass QL (Query Language) is how we ask OpenStreetMap for data.
    
    The query below asks for:
      - node   → a single point on the map (bus stops are stored as points)
      - ["highway"="bus_stop"]  → only nodes tagged as bus stops
      - (south,west,north,east) → only within this rectangle
      - out body → return full data including tags

    We also query "public_transport"="stop_position" because some mappers
    use this newer tag instead of the older "highway"="bus_stop".

    Parameters
    ----------
    bbox : tuple of (south, west, north, east) in decimal degrees

    Returns
    -------
    str : Overpass QL query
    """
    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"

    query = f"""
    [out:json][timeout:60];
    (
      node["highway"="bus_stop"]({bbox_str});
      node["public_transport"="stop_position"]({bbox_str});
    );
    out body;
    """
    return query.strip()


# ──────────────────────────────────────────────────────────────
# STEP 2 — CALL THE OVERPASS API
# ──────────────────────────────────────────────────────────────

def fetch_from_overpass(query, use_mirror=False):
    """
    Send the query to the Overpass API and return the parsed JSON response.

    We use Python's built-in urllib (no extra library needed) to make
    an HTTP POST request. The query is sent as form data.

    Parameters
    ----------
    query      : str  — the Overpass QL query string
    use_mirror : bool — use backup server if True

    Returns
    -------
    dict : parsed JSON response from the API

    Raises
    ------
    SystemExit if the request fails after retrying
    """
    url = OVERPASS_MIRROR if use_mirror else OVERPASS_URL

    # Encode query as form data (same as submitting a web form)
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")

    print(f"  Sending request to: {url}")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw)

    except urllib.error.HTTPError as e:
        # HTTP 429 = Too Many Requests, HTTP 504 = Server busy
        if e.code in (429, 504) and not use_mirror:
            print(f"  Server busy (HTTP {e.code}). Retrying with mirror in 5s...")
            time.sleep(5)
            return fetch_from_overpass(query, use_mirror=True)
        print(f"\nERROR: HTTP {e.code} — {e.reason}")
        print("The Overpass server may be overloaded. Try again in a few minutes.")
        sys.exit(1)

    except urllib.error.URLError as e:
        print(f"\nERROR: Could not connect — {e.reason}")
        print("Check your internet connection and try again.")
        sys.exit(1)

    except json.JSONDecodeError:
        print("\nERROR: Received an invalid response from the API.")
        print("The server may be overloaded. Try again in a few minutes.")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────
# STEP 3 — PARSE THE RESPONSE
# ──────────────────────────────────────────────────────────────

def parse_stops(api_response, limit=None):
    """
    Extract bus stop data from the Overpass API JSON response.

    The API returns a dict like:
    {
        "elements": [
            {
                "type": "node",
                "id": 123456789,
                "lat": 12.9716,
                "lon": 77.5946,
                "tags": {
                    "highway": "bus_stop",
                    "name": "Majestic",
                    ...
                }
            },
            ...
        ]
    }

    We extract id, lat, lon from each element.

    Parameters
    ----------
    api_response : dict — parsed JSON from Overpass
    limit        : int or None — cap on number of results

    Returns
    -------
    list of dicts, each with: id, latitude, longitude
    """
    elements = api_response.get("elements", [])

    if not elements:
        return []

    stops = []
    seen_ids = set()  # deduplicate (both tags can match the same node)

    for element in elements:
        node_id = element.get("id")

        # Skip duplicates (a node can match both tag queries)
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)

        stops.append({
            "id":        node_id,
            "latitude":  element.get("lat"),
            "longitude": element.get("lon"),
        })

        if limit and len(stops) >= limit:
            break

    return stops


# ──────────────────────────────────────────────────────────────
# STEP 4 — SAVE TO CSV
# ──────────────────────────────────────────────────────────────

def save_to_csv(stops, filepath):
    """
    Write the list of bus stops to a CSV file.

    The CSV will have exactly three columns:
        id, latitude, longitude

    Parameters
    ----------
    stops    : list of dicts from parse_stops()
    filepath : str — path to write the CSV file
    """
    fieldnames = ["id", "latitude", "longitude"]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(stops)

    print(f"  Saved → {filepath}")


# ──────────────────────────────────────────────────────────────
# STEP 5 — PRINT SUMMARY
# ──────────────────────────────────────────────────────────────

def print_summary(stops, city_name):
    """
    Print a readable table of the first 10 stops and a total count.
    """
    divider = "─" * 52
    print(f"\n{divider}")
    print(f"  {'ID':<15}  {'Latitude':>10}  {'Longitude':>11}")
    print(divider)

    # Show first 10 rows as a preview
    preview = stops[:10]
    for stop in preview:
        print(
            f"  {str(stop['id']):<15}  "
            f"{stop['latitude']:>10.5f}  "
            f"{stop['longitude']:>11.5f}"
        )

    if len(stops) > 10:
        print(f"  ... and {len(stops) - 10} more rows in the CSV")

    print(divider)
    print(f"  Total bus stops collected ({city_name}): {len(stops)}")
    print(divider + "\n")


# ──────────────────────────────────────────────────────────────
# COMMAND-LINE INTERFACE
# ──────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch bus stop locations from OpenStreetMap",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python fetch_bus_stops.py
  python fetch_bus_stops.py --city mumbai
  python fetch_bus_stops.py --city london --output london_stops.csv
  python fetch_bus_stops.py --city bengaluru --limit 100

Available cities:
  bengaluru, mumbai, delhi, chennai, hyderabad,
  kolkata, pune, london, new york, berlin
        """,
    )

    parser.add_argument(
        "--city",
        default="bengaluru",
        metavar="CITY",
        help='City name (default: bengaluru). See list above.',
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="FILE",
        help="Output CSV filename (default: {city}_bus_stops.csv)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Max stops to collect (default: all)",
    )

    return parser.parse_args()


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    city = args.city.lower().strip()

    # ── Validate city ────────────────────────────────────────
    if city not in CITY_BBOXES:
        available = ", ".join(sorted(CITY_BBOXES.keys()))
        print(f"ERROR: Unknown city '{city}'.")
        print(f"Available cities: {available}")
        print("Or edit CITY_BBOXES in the script to add your own.")
        sys.exit(1)

    bbox = CITY_BBOXES[city]
    output_file = args.output or f"{city.replace(' ', '_')}_bus_stops.csv"

    # ── Header ──────────────────────────────────────────────
    print("\n" + "=" * 52)
    print("  Transit Stop Identification — Data Collector")
    print("=" * 52)
    print(f"  City    : {city.title()}")
    print(f"  BBox    : {bbox}")
    print(f"  Output  : {output_file}")
    if args.limit:
        print(f"  Limit   : {args.limit} stops")
    print()

    # ── Step 1: Build query ──────────────────────────────────
    print("[1/4] Building Overpass query...")
    query = build_query(bbox)

    # ── Step 2: Fetch from API ───────────────────────────────
    print("[2/4] Fetching from OpenStreetMap...")
    response = fetch_from_overpass(query)

    # ── Step 3: Parse results ────────────────────────────────
    print("[3/4] Parsing results...")
    stops = parse_stops(response, limit=args.limit)

    if not stops:
        print(f"\nNo bus stops found for '{city}'.")
        print("Try a different city or check your internet connection.")
        sys.exit(0)

    # ── Step 4: Save CSV ─────────────────────────────────────
    print("[4/4] Saving to CSV...")
    save_to_csv(stops, output_file)

    # ── Summary ──────────────────────────────────────────────
    print_summary(stops, city.title())
    print(f"Done! Open '{output_file}' to see all stops.\n")


if __name__ == "__main__":
    main()
