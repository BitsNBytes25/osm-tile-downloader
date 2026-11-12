# OSM Tile Downloader

A small command-line tool that downloads OpenStreetMap / OpenTopoMap raster tiles for a
region (bounding box) across a range of zoom levels, so you can use them offline.

Originally derived from [JaeYeong An's `osm-tile-downloader`](https://github.com/goodahn/osm-tile-downloader).

## What it does

For a given region and zoom range, it computes every map tile that covers that region,
downloads each tile from a tile server, and stores it locally under the standard
`{zoom}/{x}/{y}.png` path convention.

- **Resumable** – tiles that already exist on disk are skipped, so you can interrupt
  and re-run the same command to pick up where you left off.
- **Server-friendly** – a delay (default **500ms**) is inserted between tile
  requests so as not to overwhelm the server.
- **Safe writes** – each tile is written to a temporary `.part` file and only
  atomically renamed once the download is complete, so a crash never leaves a
  truncated tile behind.
- **Retries** – transient failures are retried with exponential backoff.

## Requirements

- Python 3 (uses only the standard library — `argparse`, `urllib`, etc.).
  No `pip install` needed.
- An internet connection (needed to reach the tile server, and — if you don't
  pass `--bbox` — to geolocate your IP).

## Quick start

```bash
python3 download.py
```

With no arguments this:

1. Auto-detects your approximate location (via OS tools, falling back to
   `ip-api.com` / `ipinfo.io`).
2. For **each** zoom level, builds a centered region whose radius tapers with
   zoom — a wide swath at low zoom down to a ~10 km focus at z17 (a "per-zoom
   auto scale"). Pass `--extent N` to force a single uniform ±N° box instead.
3. Downloads **OpenTopoMap** tiles (default map type) for zoom 3–15.

If you know your coordinates, just pass a bounding box:

```bash
python3 download.py --bbox MIN_LON MIN_LAT MAX_LON MAX_LAT
```

> **Order matters:** longitude first, then latitude, then max longitude, then
> max latitude — `MIN_LON MIN_LAT MAX_LON MAX_LAT`.

## Command-line options

| Option          | Type   | Default          | Description                                                                 |
|-----------------|--------|------------------|-----------------------------------------------------------------------------|
| `--min-zoom`    | int    | 3                | Minimum zoom level (lower = less detail, fewer tiles).                        |
| `--max-zoom`    | int    | 15               | Maximum zoom level (higher = more detail, far more tiles).                   |
| `--bbox`        | float  | *(auto-detected)*| Bounding box: `MIN_LON MIN_LAT MAX_LON MAX_LAT`. If omitted, location is auto-detected. |
| `--extent`      | float  | None (auto scale)| Bounding-box half-width in degrees; used with omitted `--bbox` to force a **single uniform** region. Omit it to use the per-zoom auto scale instead. |
| `--map-type`    | str    | `topo`           | `topo` (OpenTopoMap) or `standard` (OpenStreetMap).                        |
| `--url-template`| str    | *(auto)*         | Optional tile-server URL template with `{z}/{x}/{y}` placeholders, overrides the default for `--map-type`. |
| `--output-dir`  | str    | `./map_tiles`    | Directory where tiles are written (existing files are skipped).               |
| `--delay`       | float  | 1.0              | Seconds to sleep between tile requests (politeness / rate limit).            |
| `--retries`     | int    | 3                | Max download attempts per tile before giving up.                             |

### Map types and licenses

- **`--map-type topo`** (default): OpenTopoMap topographic tiles —
  `https://tile.opentopomap.org/{z}/{x}/{y}.png`.
  Licensed **CC-BY-SA** — you must credit `opentopomap.org` if you reuse the tiles.
  Stored under `{output_dir}/topo/{z}/{x}/{y}.png`.
- **`--map-type standard`**: Standard OpenStreetMap tiles —
  `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.
  Follows the [OpenStreetMap usage policy](https://operations.osmfoundation.org/policies/tiles/)
  (for personal/offline use this is fine; heavy use requires attribution and
  a courtesy rate limit). Stored under `{output_dir}/{z}/{x}/{y}.png`.

## Examples

### Download around a specific city

```bash
python3 download.py --bbox 126.94 37.48 126.945 37.50
```

### Wider zoom range

```bash
python3 download.py --bbox -0.15 51.47 0.15 51.60 --min-zoom 4 --max-zoom 16
```

### Use standard OSM tiles instead of OpenTopoMap

```bash
python3 download.py --bbox 126.94 37.48 126.945 37.50 --map-type standard
```

### A different output location

```bash
python3 download.py --bbox 126.94 37.48 126.945 37.50 --output-dir ./my_tiles
```

### A uniform region centered on your current location

`--extent` forces a **single fixed** radius for every zoom. Omit it to use the
per-zoom auto scale instead. For a ±3° uniform box at z3–13:

```bash
python3 download.py --extent 3.0 --min-zoom 3 --max-zoom 13
```

### Be extra gentle on the server (or go a bit faster)

```bash
python3 download.py --bbox ... --delay 2.0   # slower / kinder
python3 download.py --bbox ... --delay 0.2   # faster
```

## Output layout

Tiles are stored using the standard `{zoom}/{x}/{y}.png` tile convention:

```
map_tiles/
└── topo/                  # only for --map-type topo
    ├── 3/
    │   └── 2/
    │       └── 2.png
    ├── 15/
    │   └── 27938/
    │       └── 12698.png
    └── ...
```

- For **`standard`** tiles the `topo/` level is omitted:
  `map_tiles/{z}/{x}/{y}.png`.

Example: with a default run centered on an auto-detected location (Columbus,
OH ≈ 40.03, −82.88), the z=3 tile for that region is `map_tiles/topo/3/2/2.png`.

## How long will it take?

The number of tiles grows roughly **4× per zoom level**. The default per-zoom
auto scale (no `--bbox`, no `--extent`) tapers the region from a wide low-zoom
swath down to ~10 km at z17, so the **default z3–15 run is only a few thousand
tiles** (dominated by the top zoom) instead of the tens of thousands a flat
±1° box would require. As a rough guide for a fixed **uniform** ±1° (2°×2°)
box (force one with `--extent 1.0`):

| Zoom range | Approx. tiles | Time at 1 s/tile |
|------------|---------------|------------------|
| z 3–12     | ~1,000        | ~17 min          |
| z 3–14     | ~15,000       | ~4 h             |
| z 3–15     | ~58,000       | ~16 h            |

The program prints an estimated duration before starting, so you can sanity-check
a range before committing. To limit scope, lower `--max-zoom`.

## Output behavior

On run, you'll see:

```
downloading 2 tile(s) (of 2 in bbox, 0 already present) at ~1.0s/request; estimated 0.1 min
[1/2] 1 fetched, 0 skipped, 0 failed  (z=15 x=27938 y=12698)
[2/2] 2 fetched, 0 skipped, 0 failed  (z=15 x=27938 y=12699)
done: 2/2 downloaded, 0 skipped (already present), 0 failed (out of 2 tile(s) in bbox).
```

- A **live progress line** is updated per tile.
- Re-running a finished region prints `nothing to download: all N tile(s) already present`.
- **Exit code:** `0` on full success, `1` if any tile failed (useful for scripting).

## Notes & tips

- **Location detection** may return an approximate coordinate (IP-based).
  If that's wrong for your use case, always pass `--bbox` explicitly.
- **Resuming is free.** Because existing files are skipped, re-running after a
  partial download simply continues with the missing tiles.
- **Custom tile servers.** Use `--url-template` to point at any server that
  follows the `/{z}/{x}/{y}.png` convention (e.g., a self-hosted Mapnik tile cache).
- **`--delay 0`** disables the inter-request pause — only if your server allows it.

## Attribution (if you reuse the tiles)

- **OpenTopoMap tiles** require credit to [opentopomap.org](https://opentopomap.org)
  (CC-BY-SA).
- **OpenStreetMap tiles** are © OpenStreetMap contributors — see the
  [OpenStreetMap attribution](https://www.openstreetmap.org/copyright) and
  [usage policy](https://operations.osmfoundation.org/policies/tiles/).


## AI Disclaimer

This code is modified with Bonsai-2 27b because I'm feeling lazy for this project.
Code auditing, testing, and commits still manually performed.