# download.py
#
# MIT License
# Copyright (c) 2026
#
# Originally from JaeYeong An's https://github.com/goodahn/osm-tile-downloader
import math
import os
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request

DEFAULT_URL_TEMPLATES = {
	"topo": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
	"standard": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
}

USER_AGENT = "osm-tile-downloader/1.0"

# Automatic per-zoom region size (used only when --bbox is omitted and
# --extent is not set). A geometric "sliding scale": a wide swath at low zoom
# tapers to a tight ~10 km focus at z17 so high-zoom downloads stay small.
AUTO_SCALE_LOW_ZOOM   = 3     # zoom at which the widest swath is used
AUTO_SCALE_LOW_KM     = 300.0 # swath radius in km at the low zoom
AUTO_SCALE_HIGH_ZOOM  = 17    # zoom at which the focus tightens
AUTO_SCALE_HIGH_KM    = 10.0  # focus radius in km at the high zoom
KM_PER_DEG = 111.32


def auto_radius_km(zoom):
	"""Radius (km) of the auto-download region for a single zoom level.

	Geometric (log-linear) taper from a wide swath (AUTO_SCALE_LOW_KM at
	AUTO_SCALE_LOW_ZOOM) to a tight focus (AUTO_SCALE_HIGH_KM at
	AUTO_SCALE_HIGH_ZOOM); clamped to the endpoints outside that range.
	"""
	if zoom <= AUTO_SCALE_LOW_ZOOM:
		return AUTO_SCALE_LOW_KM
	if zoom >= AUTO_SCALE_HIGH_ZOOM:
		return AUTO_SCALE_HIGH_KM
	t = (zoom - AUTO_SCALE_LOW_ZOOM) / (AUTO_SCALE_HIGH_ZOOM - AUTO_SCALE_LOW_ZOOM)
	return AUTO_SCALE_LOW_KM * (AUTO_SCALE_HIGH_KM / AUTO_SCALE_LOW_KM) ** t


def lonlat_to_tile_coords(lon, lat, zoom):
	"""
	경위도를 타일 좌표(x, y)로 변환합니다.
	Converts latitude and longitude into tile coordinates (x, y).
	"""
	n = 2.0 ** zoom
	xtile = int(n * ((lon + 180.0) / 360.0))
	ytile = int(n * (1.0 - math.log(math.tan(math.radians(lat)) + (1.0 / math.cos(math.radians(lat)))) / math.pi) / 2.0)
	return xtile, ytile


_OS_LOC_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _parse_os_location(output):
	"""Parse latitude/longitude from OS-native geolocation tool output."""
	for line in output.splitlines():
		tokens = _OS_LOC_RE.findall(line)
		if len(tokens) >= 2:
			try:
				lat, lng = float(tokens[0]), float(tokens[1])
			except ValueError:
				continue
			if -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0:
				return lat, lng
	return None


def _query_ip_location(url, timeout):
	"""Query a public IP-geolocation API. Returns (lat, lng) or None."""
	req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:
			data = json.loads(resp.read().decode("utf-8"))
	except Exception:
		return None
	if not isinstance(data, dict):
		return None
	try:
		if "lat" in data and "lon" in data:
			return float(data["lat"]), float(data["lon"])
		loc = data.get("loc")
		if isinstance(loc, str):
			lat_s, lon_s = loc.split(",", 1)
			return float(lat_s), float(lon_s)
	except (TypeError, ValueError):
		return None


def detect_location(timeout=5):
	"""
	Detect the user's approximate (lat, lng) from the operating system.

	Tries OS-native location tools first (if available), then falls back to
	IP-based geolocation. Returns (lat, lng) or None if nothing works.
	"""
	for cmd in (["geolocate", "ip"], ["geo"], ["geolookup"]):
		if not shutil.which(cmd[0]):
			continue
		try:
			proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
		except (OSError, subprocess.TimeoutExpired):
			continue
		if proc.returncode != 0:
			continue
		location = _parse_os_location(proc.stdout)
		if location is not None:
			return location
	for url in ("http://ip-api.com/json", "https://ipinfo.io/json"):
		location = _query_ip_location(url, timeout)
		if location is not None:
			return location
	return None


def fetch_tile(url, output_path, delay, retries, timeout=30):
	"""
	한 타일을 url에서 output_path로 다운로드한다.
	Downloads a single tile from ``url`` into ``output_path``.

	Downloads are written to a ``.part`` temporary file and atomically renamed
	only once complete, so a crashed run never leaves a truncated tile in place.
	Transient failures are retried with an increasing backoff; the last failure
	is re-raised when all attempts are exhausted.
	"""
	last_exc = None
	for attempt in range(1, retries + 1):
		tmp_path = output_path + ".part"
		try:
			req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
			with urllib.request.urlopen(req, timeout=timeout) as resp:
				data = resp.read()
			with open(tmp_path, "wb") as tmp_file:
				tmp_file.write(data)
			# 원본 타일 위치로 교체 (완료 후에만 수행)
			os.replace(tmp_path, output_path)
			return
		except Exception as exc:
			last_exc = exc
			if attempt < retries:
				wait = min(delay * (2.0 ** attempt), 30.0)
				print(
					f"  {type(exc).__name__}: {exc} (retry {attempt}/{retries} in {wait:.1f}s)",
					file=sys.stderr,
				)
				time.sleep(wait)
	# 재시도 모두 실패: 마지막 예외를 던진다
	if last_exc is not None:
		raise last_exc


def main():
	"""
	주어진 경계상자와 줌 레벨에 대해 타일을 서버에서 다운로드한다.
	Downloads OSM tiles for a given bounding box and zoom levels.

	When --bbox is omitted, the location is auto-detected and every zoom level
	is centered on it. The region radius then tapers with zoom (a wide swath at
	low zoom down to a ~10 km focus at z17) unless a fixed --extent is
	supplied.

	Supports two map types, chosen with --map-type:
	* 'topo' (default): OpenTopoMap topographic tiles,
	  https://tile.opentopomap.org/{z}/{x}/{y}.png,
	  stored under map_tiles/topo/{z}/{x}/{y}.png.
	  These tiles are licensed CC-BY-SA; always name opentopomap.org.
	* 'standard': standard OpenStreetMap tiles,
	  https://tile.openstreetmap.org/{z}/{x}/{y}.png,
	  stored under map_tiles/{z}/{x}/{y}.png.

	Downloads are slow on purpose: a politeness delay (--delay, default 1.0 s)
	is inserted between tile requests so as not to overwhelm the tile server,
	and existing files are skipped so the run can be interrupted and resumed.
	"""
	parser = argparse.ArgumentParser(description="Download tiles for a given bounding box and zoom levels.")
	parser.add_argument("--min-zoom", type=int, default=3, help="Minimum zoom level (default: 3)")
	parser.add_argument("--max-zoom", type=int, default=15, help="Maximum zoom level (default: 15)")
	parser.add_argument("--bbox", type=float, nargs=4, default=None, metavar=('MIN_LON', 'MIN_LAT', 'MAX_LON', 'MAX_LAT'), help="Bounding box; if omitted, centered on the auto-detected location")
	parser.add_argument("--extent", type=float, default=None, help="Bounding-box half-width in degrees, used when --bbox is omitted to force a uniform region; omit it (default) to use a per-zoom auto scale instead")
	parser.add_argument("--map-type", type=str, choices=["topo", "standard"], default="topo", help="Map tile type: 'topo' (default) uses OpenTopoMap topographic tiles and stores them under map_tiles/topo/; 'standard' uses OpenStreetMap tiles and stores them under map_tiles/")
	parser.add_argument("--url-template", type=str, default=None, help="Tile server URL template; overrides the default for the selected --map-type")
	parser.add_argument("--output-dir", type=str, default="./map_tiles", help="Output directory for tiles (existing files are skipped)")
	parser.add_argument("--delay", type=float, default=0.5, help="Seconds to sleep between tile requests to be polite to the server (default: 0.5)")
	parser.add_argument("--retries", type=int, default=3, help="Max download attempts per tile before giving up (default: 3)")

	args = parser.parse_args()

	if args.extent is not None and args.extent <= 0:
		parser.error("--extent must be greater than 0")

	if args.min_zoom > args.max_zoom:
		parser.error("--min-zoom must be less than or equal to --max-zoom")

	if min(args.min_zoom, args.max_zoom) < 0:
		parser.error("zoom levels must be non-negative")

	if args.retries < 1:
		parser.error("--retries must be at least 1")

	# When --bbox is omitted we derive the region(s) from an auto-detected
	# location. Each zoom is centered on that location; the radius tapers with
	# zoom (per-zoom auto scale) or is held constant (--extent).
	center = None
	if args.bbox is None:
		center = detect_location()
		if center is None:
			parser.error("could not detect location; specify --bbox explicitly")
		lat, lng = center

	url_template = args.url_template or DEFAULT_URL_TEMPLATES[args.map_type]
	output_base = os.path.join(args.output_dir, "topo") if args.map_type == "topo" else args.output_dir

	os.makedirs(output_base, exist_ok=True)

	# Summarize the derived region(s) before downloading.
	# 다운로드 대상 영역(경위도 기반)을 요약하여 출력한다.
	if center is not None:
		if args.extent is not None:
			half = args.extent
			print(
				f"auto-detected location {lat:.4f}, {lng:.4f}; using uniform "
				f"bounding box {lng-half:.4f} {max(-89.99, lat-half):.4f} "
				f"{lng+half:.4f} {min(89.99, lat+half):.4f}",
				file=sys.stderr,
			)
		else:
			print(
				f"auto-detected location {lat:.4f}, {lng:.4f}; per-zoom auto scale "
				f"{auto_radius_km(args.min_zoom):.0f} km @z{args.min_zoom} -> "
				f"{auto_radius_km(args.max_zoom):.1f} km @z{args.max_zoom} "
				"(wide swath at low zoom -> tight focus at high zoom)",
				file=sys.stderr,
			)

	# 경계상자의 타일을 계산하고, 이미 존재하는 파일은 스킵
	# Compute the tiles for each zoom, skipping any file already present.
	# --bbox가 없으면 각 줌별로 중심화된 영역을 사용한다.
	tiles = []
	total = 0
	skipped = 0
	for z in range(args.min_zoom, args.max_zoom + 1):
		if args.bbox is not None:
			min_lon, min_lat, max_lon, max_lat = args.bbox
		else:
			half = args.extent if args.extent is not None else auto_radius_km(z) / KM_PER_DEG
			min_lon, min_lat, max_lon, max_lat = (
				lng - half, max(-89.99, lat - half), lng + half, min(89.99, lat + half)
			)
		x_min, y_max = lonlat_to_tile_coords(min_lon, min_lat, z)
		x_max, y_min = lonlat_to_tile_coords(max_lon, max_lat, z)
		for x in range(x_min, x_max + 1):
			for y in range(y_min, y_max + 1):
				total += 1
				output_path = os.path.join(output_base, str(z), str(x), f"{y}.png")
				# 존재하면 스킵 (이미 다운로드됨 / 이미 존재)
				if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
					skipped += 1
					continue
				# 저장 경로 디렉트를 생성
				os.makedirs(os.path.dirname(output_path), exist_ok=True)
				url = url_template.format(z=z, x=x, y=y)
				tiles.append((z, x, y, url, output_path))

	if not tiles:
		print(f"nothing to download: all {total} tile(s) already present (skipped {skipped}).")
		return

	print(
		f"downloading {len(tiles)} tile(s) "
		f"(of {total} in bbox, {skipped} already present) "
		f"at ~{args.delay:.1f}s/request; estimated {len(tiles) * args.delay / 60:.1f} min",
		file=sys.stderr,
	)

	downloaded = 0
	failed = 0
	for i, (z, x, y, url, output_path) in enumerate(tiles, 1):
		try:
			fetch_tile(url, output_path, args.delay, args.retries)
			downloaded += 1
		except Exception as exc:
			failed += 1
			print(f"failed tile z={z} x={x} y={y}: {exc}", file=sys.stderr)
		print(
			f"\r[{i}/{len(tiles)}] {downloaded} fetched, {skipped} skipped, {failed} failed  (z={z} x={x} y={y})   ",
			end="", flush=True,
		)
		# 타일 서버에 부하를 주지 않기 위해 요청 간 지연
		# Politeness delay between requests to the tile server.
		if args.delay > 0:
			time.sleep(args.delay)

	print()
	print(
		f"done: {downloaded}/{len(tiles)} downloaded, {skipped} skipped (already present), "
		f"{failed} failed (out of {total} tile(s) in bbox).",
	)
	if failed:
		sys.exit(1)

if __name__ == "__main__":
	main()
