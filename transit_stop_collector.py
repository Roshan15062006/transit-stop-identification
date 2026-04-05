"""
=============================================================
  Transit Stop Identification — Step 1
  Fetch bus stop locations from OpenStreetMap & save to CSV
  (Bonus) Download Street View images for each stop
=============================================================

QUICK START
-----------
  1. Install dependencies:
       pip install overpy requests

  2. Run (fetches stops, saves CSV):
       python transit_stop_collector.py

  3. To also download Street View images, set your API key:
       python transit_stop_collector.py --streetview YOUR_API_KEY

HOW TO GET A STREET VIEW API KEY (free tier, 100 images/month free)
---------------------------------------------------------------------
  1. Go to https://console.cloud.google.com
  2. Create a project → Enable "Street View Static API"
  3. Credentials → Create API Key → copy it

CITY BOUNDING BOXES (paste one into BBOX below)
------------------------------------------------
  Bengaluru : 12.834, 77.461, 13.139, 77.784
  Mumbai    : 18.894, 72.776, 19.270, 72.987
  Delhi     : 28.405, 76.838, 28.883, 77.346
  Chennai   : 12.900, 80.099, 13.233, 80.328
  Hyderabad : 17.243, 78.270, 17.560, 78.629
  London    : 51.450, -0.250, 51.570, -0.050
  New York  : 40.670, -74.030, 40.820, -73.870
"""

import argparse
import csv
import os
import sys
import time
import urllib.parse
import urllib.request


# ─────────────────────────────────────────────────────────────
# CONFIGURATION — edit these to change city / output location
# ─────────────────────────────────────────────────────────────

# Bounding box: (south_lat, west_lon, north_lat, east_lon)
# This covers central Bengaluru — change for your city
BBOX = (12.834, 77.461, 13.139, 77.784)

# Maximum stops to fetch (keep low while testing; remove cap for full run)
MAX_STOPS = 200

# Output CSV file
CSV_OUTPUT = "bus_stops.csv"

# Street View image settings
SV_IMAGE_SIZE  = "640x480"    # width x height (max 640x640 on free tier)
SV_HEADING     = 0            # camera direction in degrees (0 = north)
                               # set to None to let Google choose automatically
SV_PITCH       = 0            # camera tilt: 0=horizontal, 90=straight up
SV_FOV         = 90           # field of view in degrees (lower = more zoom)
SV_OUTPUT_DIR  = "streetview_images"


# ─────────────────────────────────────────────────────────────
# PART 1 — FETCH BUS STOPS FROM OPENSTREETMAP
# ─────────────────────────────────────────────────────────────

def fetch_bus_stops(bbox, max_stops=None):
    """
    Query the Overpass API for bus stop nodes within a bounding box.

    The Overpass query language lets us ask OSM for specific map
    features. Here we ask for nodes tagged highway=bus_stop or
    public_transport=stop_position (both tag variants are used
    by different mappers for the same concept).

    Parameters
    ----------
    bbox     : (south, west, north, east) in decimal degrees
    max_stops: optional integer cap on results

    Returns
    -------
    list of dicts with keys: id, lat, lon, name, operator, routes
    """
    try:
        import overpy
    except ImportError:
        print("ERROR: 'overpy' is not installed.")
        print("       Run: pip install overpy")
        sys.exit(1)

    south, west, north, east = bbox
    bbox_str = f"{south},{west},{north},{east}"

    # Overpass QL query:
    #   [out:json]           → return JSON (not XML)
    #   [timeout:60]         → wait up to 60 s for large queries
    #   node[...](bbox)      → find nodes with these tags in the bbox
    #   out body             → include tag data in the result
    query = f"""
        [out:json][timeout:60];
        (
            node["highway"="bus_stop"]({bbox_str});
            node["public_transport"="stop_position"]({bbox_str});
        );
        out body;
    """

    print(f"Querying OpenStreetMap for bus stops in bbox {bbox_str} …")
    api = overpy.Overpass()

    try:
        result = api.query(query)
    except overpy.exception.OverPyException as exc:
        print(f"Overpass API error: {exc}")
        print("The public server may be busy. Wait a minute and try again,")
        print("or use the mirror: https://overpass.kumi.systems/api/interpreter")
        sys.exit(1)

    stops = []
    for node in result.nodes:
        tags     = node.tags
        name     = tags.get("name") or tags.get("name:en") or ""
        operator = tags.get("operator", "")
        routes   = tags.get("route_ref", tags.get("routes", ""))

        stops.append({
            "id"      : node.id,
            "lat"     : float(node.lat),
            "lon"     : float(node.lon),
            "name"    : name,
            "operator": operator,
            "routes"  : routes,
        })

        if max_stops and len(stops) >= max_stops:
            break

    return stops


# ─────────────────────────────────────────────────────────────
# PART 2 — SAVE TO CSV
# ─────────────────────────────────────────────────────────────

def save_to_csv(stops, filepath):
    """
    Write the list of stop dicts to a CSV file.

    The CSV will have columns:
        id, lat, lon, name, operator, routes, streetview_url
    The streetview_url column is pre-filled with a working URL
    so you can click it without an API key (uses the embed viewer).
    """
    fieldnames = ["id", "lat", "lon", "name", "operator", "routes", "streetview_url"]

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for stop in stops:
            # Build a free clickable Street View URL (no API key needed)
            sv_url = (
                f"https://www.google.com/maps/@?api=1"
                f"&map_action=pano"
                f"&viewpoint={stop['lat']},{stop['lon']}"
            )
            writer.writerow({**stop, "streetview_url": sv_url})

    print(f"Saved {len(stops)} stops → {filepath}")


# ─────────────────────────────────────────────────────────────
# PART 3 (BONUS) — DOWNLOAD STREET VIEW IMAGES
# ─────────────────────────────────────────────────────────────

def build_streetview_url(lat, lon, api_key,
                         size=SV_IMAGE_SIZE,
                         heading=SV_HEADING,
                         pitch=SV_PITCH,
                         fov=SV_FOV):
    """
    Build a Google Street View Static API URL for a given location.

    The Street View Static API returns a JPEG image directly.
    No SDK required — just a URL fetch.

    Parameters
    ----------
    lat, lon  : decimal degrees
    api_key   : your Google Cloud API key
    size      : "WIDTHxHEIGHT" string (max 640x640 on free tier)
    heading   : compass direction for camera (0–360); None = auto
    pitch     : vertical angle (0 = horizontal, 90 = straight up)
    fov       : horizontal field of view (default 90, lower = more zoom)
    """
    base = "https://maps.googleapis.com/maps/api/streetview"
    params = {
        "location": f"{lat},{lon}",
        "size"    : size,
        "pitch"   : pitch,
        "fov"     : fov,
        "key"     : api_key,
    }
    if heading is not None:
        params["heading"] = heading

    return f"{base}?{urllib.parse.urlencode(params)}"


def build_streetview_metadata_url(lat, lon, api_key):
    """
    Build a metadata URL to check if Street View coverage exists
    at a location BEFORE downloading the image.

    This saves API quota — if status != "OK", skip the download.
    The metadata call itself is free (does not count against quota).
    """
    base = "https://maps.googleapis.com/maps/api/streetview/metadata"
    params = {
        "location": f"{lat},{lon}",
        "key"     : api_key,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"


def download_streetview_images(stops, api_key, output_dir,
                               max_images=20, delay_seconds=0.5):
    """
    Download one Street View JPEG per stop and save to output_dir.

    Workflow for each stop:
      1. Call the free metadata endpoint to check coverage exists.
      2. If coverage is confirmed, download the actual image.
      3. Save as {stop_id}.jpg

    Parameters
    ----------
    stops        : list of stop dicts from fetch_bus_stops()
    api_key      : Google Cloud API key (Street View Static API enabled)
    output_dir   : folder to save images into
    max_images   : safety cap (remove for full run)
    delay_seconds: pause between requests to avoid rate-limit errors
    """
    import json

    os.makedirs(output_dir, exist_ok=True)
    downloaded = 0
    skipped    = 0

    print(f"\nDownloading Street View images → {output_dir}/")
    print(f"(capped at {max_images} images for this demo)\n")

    for i, stop in enumerate(stops):
        if downloaded >= max_images:
            print(f"Reached cap of {max_images} images. Done.")
            break

        lat, lon = stop["lat"], stop["lon"]
        stop_id  = stop["id"]
        name     = stop["name"] or f"stop_{stop_id}"

        # ── Step 1: Check metadata (free call) ──────────────────
        meta_url = build_streetview_metadata_url(lat, lon, api_key)
        try:
            with urllib.request.urlopen(meta_url, timeout=10) as resp:
                meta = json.loads(resp.read().decode())
        except Exception as exc:
            print(f"  [{i+1}] SKIP  {name[:40]} — metadata error: {exc}")
            skipped += 1
            continue

        if meta.get("status") != "OK":
            # No Street View coverage at this location
            print(f"  [{i+1}] SKIP  {name[:40]} — no coverage ({meta.get('status')})")
            skipped += 1
            continue

        # ── Step 2: Download the image ───────────────────────────
        img_url  = build_streetview_url(lat, lon, api_key)
        img_path = os.path.join(output_dir, f"{stop_id}.jpg")

        try:
            with urllib.request.urlopen(img_url, timeout=15) as resp:
                image_data = resp.read()

            with open(img_path, "wb") as f:
                f.write(image_data)

            size_kb = len(image_data) // 1024
            print(f"  [{i+1}] OK    {name[:40]:40s}  {size_kb:4d} KB  → {stop_id}.jpg")
            downloaded += 1

        except Exception as exc:
            print(f"  [{i+1}] ERROR {name[:40]} — {exc}")
            skipped += 1

        # Be polite to the API — avoid hammering the server
        time.sleep(delay_seconds)

    print(f"\nDone. Downloaded: {downloaded}  |  Skipped: {skipped}")
    return downloaded


# ─────────────────────────────────────────────────────────────
# DEMO MODE — runs without an internet connection
# ─────────────────────────────────────────────────────────────

DEMO_STOPS = [
    {"id": 1001, "lat": 12.9716, "lon": 77.5946, "name": "Majestic Bus Stand",
     "operator": "BMTC", "routes": "1,2,5,10"},
    {"id": 1002, "lat": 12.9784, "lon": 77.6408, "name": "Indiranagar 100ft Rd",
     "operator": "BMTC", "routes": "300G,314"},
    {"id": 1003, "lat": 12.9352, "lon": 77.6245, "name": "Koramangala 5th Block",
     "operator": "BMTC", "routes": "500C,550"},
    {"id": 1004, "lat": 13.0298, "lon": 77.5792, "name": "Yeshwanthpur Circle",
     "operator": "BMTC", "routes": "224,225"},
    {"id": 1005, "lat": 12.9259, "lon": 77.5002, "name": "Banashankari Stage 2",
     "operator": "BMTC", "routes": "37,37E"},
]


def print_summary(stops, csv_path):
    """Print a readable summary table to the terminal."""
    print("\n" + "─" * 72)
    print(f"  {'ID':<12}  {'Name':<30}  {'Lat':>9}  {'Lon':>9}")
    print("─" * 72)
    for s in stops[:20]:  # show first 20 in terminal
        name = (s["name"] or "—")[:29]
        print(f"  {str(s['id']):<12}  {name:<30}  {s['lat']:>9.5f}  {s['lon']:>9.5f}")
    if len(stops) > 20:
        print(f"  … and {len(stops) - 20} more (see {csv_path})")
    print("─" * 72)
    print(f"  Total stops: {len(stops)}")
    print("─" * 72 + "\n")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch transit stop locations from OpenStreetMap",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--streetview", metavar="API_KEY",
        help="Google Street View API key. If provided, images are downloaded.",
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Run with built-in sample data (no internet required).",
    )
    parser.add_argument(
        "--city", metavar="CITY",
        choices=["bengaluru", "mumbai", "delhi", "chennai",
                 "hyderabad", "london", "newyork"],
        default="bengaluru",
        help="City preset for bounding box (default: bengaluru).",
    )
    parser.add_argument(
        "--max", type=int, default=MAX_STOPS,
        help=f"Max stops to fetch (default: {MAX_STOPS}).",
    )
    parser.add_argument(
        "--output", default=CSV_OUTPUT,
        help=f"CSV output path (default: {CSV_OUTPUT}).",
    )
    return parser.parse_args()


CITY_BBOXES = {
    "bengaluru" : (12.834, 77.461, 13.139, 77.784),
    "mumbai"    : (18.894, 72.776, 19.270, 72.987),
    "delhi"     : (28.405, 76.838, 28.883, 77.346),
    "chennai"   : (12.900, 80.099, 13.233, 80.328),
    "hyderabad" : (17.243, 78.270, 17.560, 78.629),
    "london"    : (51.450, -0.250, 51.570, -0.050),
    "newyork"   : (40.670, -74.030, 40.820, -73.870),
}


def main():
    args = parse_args()

    print("\n" + "=" * 56)
    print("  Transit Stop Collector — OpenStreetMap Edition")
    print("=" * 56 + "\n")

    # ── Fetch or load stops ──────────────────────────────────
    if args.demo:
        print("Running in DEMO mode (no internet required).\n")
        stops = DEMO_STOPS
    else:
        bbox = CITY_BBOXES.get(args.city, BBOX)
        stops = fetch_bus_stops(bbox, max_stops=args.max)

        if not stops:
            print("No stops found. Try a different city or larger bounding box.")
            sys.exit(0)

        print(f"Found {len(stops)} bus stops.")

    # ── Print summary ────────────────────────────────────────
    print_summary(stops, args.output)

    # ── Save to CSV ──────────────────────────────────────────
    save_to_csv(stops, args.output)

    # ── Street View images (optional) ────────────────────────
    if args.streetview:
        n = download_streetview_images(
            stops,
            api_key    = args.streetview,
            output_dir = SV_OUTPUT_DIR,
            max_images = 20,        # increase for a real run
        )
        print(f"\nImages saved to: {SV_OUTPUT_DIR}/")
    else:
        print("\nTip: add --streetview YOUR_API_KEY to also download images.")
        print("     Metadata & clickable URLs are already in the CSV.\n")

    print(f"All done! Open '{args.output}' to see your stop data.\n")


if __name__ == "__main__":
    main()
