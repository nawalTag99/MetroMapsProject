"""Heilbronn Transport Network Data Loader (rewritten)

- Builds a station graph from either:
  * OpenStreetMap public-transport route relations via Overpass/Overpy, or
  * GTFS files (stops/routes/trips/stop_times).

Key improvements vs the original version:
- Deduplicates "mutual"/co-located stops (e.g., OSM stop_position vs platform nodes)
  into ONE canonical station node (distance-based, optional name check).
- GTFS: collapses platform stops into their parent_station when available.
- Uses O(1) edge merging via a dict instead of O(E^2) scan.
- More robust Overpy relation-member handling (attempts member.resolve()).

Output JSON format (compatible with your pipeline):
{
  "name": "...",
  "stations": [{"id","name","x","y","lines":[...]}, ...],
  "edges": [{"from_id","to_id","lines":[...]}, ...]
}

Notes:
- Coordinates are stored as scaled lon/lat (lon*100000, lat*100000) then normalized
  to start from the origin, matching your original conventions.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# -----------------------------
# Stop de-duplication
# -----------------------------

def _norm_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


@dataclass
class _StationRec:
    id: str
    name: str
    lon: float
    lat: float
    x_scaled: float
    y_scaled: float
    lines: List[str]


class StopDeduper:
    """Deduplicate stops that are geographically very close.

    This is especially important for OSM public transport where the same physical
    stop can appear as both a stop_position node and a platform node.

    Uses a simple spatial hash (grid) in meters + neighbor search.

    Parameters
    ----------
    merge_distance_m: float
        Maximum distance (meters) to consider two stops the same.
    require_name_match: bool
        If True, when both candidate names are non-empty, they must match loosely
        (normalized equality or containment). Helps prevent accidental merges.
    """

    def __init__(self, merge_distance_m: float = 50.0, require_name_match: bool = False):
        self.merge_distance_m = float(merge_distance_m)
        self.require_name_match = bool(require_name_match)

        self._lat0: Optional[float] = None
        self._lon0: Optional[float] = None

        # canonical_id -> station record
        self._stations: Dict[str, _StationRec] = {}

        # spatial hash: (gx, gy) -> list[canonical_id]
        self._cell: Dict[Tuple[int, int], List[str]] = defaultdict(list)

        # original_id -> canonical_id (useful for debugging)
        self.id_map: Dict[str, str] = {}

    def _xy_m(self, lat: float, lon: float) -> Tuple[float, float]:
        # Local equirectangular projection relative to the first seen point
        if self._lat0 is None:
            self._lat0, self._lon0 = lat, lon
        assert self._lon0 is not None and self._lat0 is not None
        k = 111_320.0
        x = (lon - self._lon0) * k * math.cos(math.radians(self._lat0))
        y = (lat - self._lat0) * k
        return x, y

    def _cell_key(self, x_m: float, y_m: float) -> Tuple[int, int]:
        s = self.merge_distance_m
        return int(math.floor(x_m / s)), int(math.floor(y_m / s))

    @staticmethod
    def _dist2(ax: float, ay: float, bx: float, by: float) -> float:
        dx, dy = ax - bx, ay - by
        return dx * dx + dy * dy

    def _names_compatible(self, a: str, b: str) -> bool:
        if not self.require_name_match:
            return True
        na, nb = _norm_name(a), _norm_name(b)
        if not na or not nb:
            return True
        return na == nb or na in nb or nb in na

    def upsert(
        self,
        original_id: str,
        name: str,
        lat: float,
        lon: float,
        prefer_canonical_id: Optional[str] = None,
    ) -> str:
        """Return canonical station id for a stop; create/merge as needed."""

        x_m, y_m = self._xy_m(lat, lon)
        gx, gy = self._cell_key(x_m, y_m)
        r2 = self.merge_distance_m * self.merge_distance_m

        best_id: Optional[str] = None
        best_d2: Optional[float] = None

        # Search neighboring cells
        for nx in (gx - 1, gx, gx + 1):
            for ny in (gy - 1, gy, gy + 1):
                for cid in self._cell.get((nx, ny), []):
                    s = self._stations[cid]
                    sx_m, sy_m = self._xy_m(s.lat, s.lon)
                    d2 = self._dist2(x_m, y_m, sx_m, sy_m)
                    if d2 <= r2 and self._names_compatible(name, s.name):
                        if best_d2 is None or d2 < best_d2:
                            best_id, best_d2 = cid, d2

        if best_id is not None:
            self.id_map[original_id] = best_id
            return best_id

        # Create new canonical station
        cid = str(prefer_canonical_id or original_id)

        # Ensure uniqueness if prefer_canonical_id collides
        if cid in self._stations:
            i = 2
            base = cid
            while f"{base}#{i}" in self._stations:
                i += 1
            cid = f"{base}#{i}"

        rec = _StationRec(
            id=cid,
            name=name or f"Stop_{original_id}",
            lon=float(lon),
            lat=float(lat),
            x_scaled=float(lon) * 100000.0,
            y_scaled=float(lat) * 100000.0,
            lines=[],
        )
        self._stations[cid] = rec
        self._cell[(gx, gy)].append(cid)
        self.id_map[original_id] = cid
        return cid

    def add_line(self, canonical_id: str, line: str):
        rec = self._stations.get(canonical_id)
        if not rec:
            return
        if line not in rec.lines:
            rec.lines.append(line)

    def stations_as_dict(self) -> Dict[str, dict]:
        return {
            cid: {
                "id": rec.id,
                "name": rec.name,
                "x": rec.x_scaled,
                "y": rec.y_scaled,
                "lines": sorted(rec.lines),
            }
            for cid, rec in self._stations.items()
        }


# -----------------------------
# Utility: edges
# -----------------------------

def _edge_key(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _add_edge(edges: Dict[Tuple[str, str], set], a: str, b: str, line: str):
    if a == b:
        return
    k = _edge_key(a, b)
    edges[k].add(line)


def _normalize_station_coordinates(stations: Dict[str, dict]):
    if not stations:
        return
    xs = [s["x"] for s in stations.values()]
    ys = [s["y"] for s in stations.values()]
    min_x, min_y = min(xs), min(ys)
    for s in stations.values():
        s["x"] -= min_x
        s["y"] -= min_y


# -----------------------------
# OSM loader
# -----------------------------

def fetch_heilbronn_osm_data(
    save_path: str = "heilbronn_network.json",
    merge_distance_m: float = 50.0,
    require_name_match: bool = False,
):
    """Fetch Heilbronn public transport data from OpenStreetMap via Overpass.

    Deduplicates co-located stops to avoid stacked nodes.
    """

    # Lazy import so the file can be used without OSM deps.
    import overpy

    overpass_query = r"""
    [out:json][timeout:25];
    area["name"="Heilbronn"]["admin_level"="6"]->.searchArea;
    (
      relation["route"="tram"](area.searchArea);
      relation["route"="light_rail"](area.searchArea);
      relation["route"="bus"]["ref"~"^[0-9]$"](area.searchArea);
    );
    out body;
    >;
    out skel qt;
    """

    print("Fetching Heilbronn transport data from OpenStreetMap...")

    api = overpy.Overpass()

    try:
        result = api.query(overpass_query)

        deduper = StopDeduper(merge_distance_m=merge_distance_m, require_name_match=require_name_match)
        edges: Dict[Tuple[str, str], set] = defaultdict(set)

        for relation in result.relations:
            route_ref = relation.tags.get("ref") or relation.tags.get("name") or "Unknown"
            route_type = relation.tags.get("route", "bus")
            print(f"Processing route: {route_ref} ({route_type})")

            stops: List[str] = []

            for member in relation.members:
                if member.role not in ("stop", "platform", ""):
                    continue

                # Resolve Overpy relation member into Node/Way if possible.
                obj = None
                try:
                    obj = member.resolve()
                except Exception:
                    obj = getattr(member, "ref", None)

                # We only handle nodes (have lat/lon).
                if not hasattr(obj, "lat") or not hasattr(obj, "lon"):
                    continue

                tags = getattr(obj, "tags", {}) or {}
                stop_name = tags.get("name") or f"Stop_{getattr(obj, 'id', 'unknown')}"

                lat = float(obj.lat)
                lon = float(obj.lon)
                original_id = str(getattr(obj, "id", "unknown"))

                cid = deduper.upsert(original_id=original_id, name=stop_name, lat=lat, lon=lon)
                deduper.add_line(cid, route_ref)
                stops.append(cid)

            # Create edges between consecutive canonical stops.
            # Avoid duplicate consecutive stations after merging.
            filtered: List[str] = []
            for s in stops:
                if not filtered or filtered[-1] != s:
                    filtered.append(s)
            stops = filtered

            for i in range(len(stops) - 1):
                _add_edge(edges, stops[i], stops[i + 1], route_ref)

        stations = deduper.stations_as_dict()
        _normalize_station_coordinates(stations)

        edges_list = [
            {"from_id": u, "to_id": v, "lines": sorted(list(lines))}
            for (u, v), lines in edges.items()
        ]

        network_data = {
            "name": "Heilbronn Public Transport (OSM)",
            "stations": list(stations.values()),
            "edges": edges_list,
        }

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(network_data, f, indent=2, ensure_ascii=False)

        print(
            f"\nSuccess! Saved {len(stations)} stations and {len(edges_list)} edges to {save_path}"
        )
        print(f"Dedup merged {len(deduper.id_map)} raw stop-ids into {len(stations)} canonical stations.")
        return network_data

    except Exception as e:
        print(f"Error fetching OSM data: {e}")
        print("\nCreating sample Heilbronn-like network instead...")
        return create_heilbronn_sample(save_path)


# -----------------------------
# Sample network
# -----------------------------

def create_heilbronn_sample(save_path: str = "heilbronn_network.json"):
    """Create a sample Heilbronn-inspired network."""

    stations = [
        {"id": "HBF", "name": "Hauptbahnhof", "x": 200, "y": 250, "lines": ["1", "2", "3", "4"]},
        {"id": "HT", "name": "Harmonie/Theater", "x": 280, "y": 240, "lines": ["1", "2"]},
        {"id": "RAT", "name": "Rathaus", "x": 350, "y": 230, "lines": ["1", "2", "3"]},
        {"id": "WOL", "name": "Wollhaus", "x": 420, "y": 220, "lines": ["1", "3"]},
        {"id": "OEH", "name": "Öhringen", "x": 500, "y": 200, "lines": ["1"]},
        {"id": "KIL", "name": "Kiliansplatz", "x": 250, "y": 320, "lines": ["2", "4"]},
        {"id": "BOE", "name": "Böckingen Rathaus", "x": 180, "y": 380, "lines": ["2"]},
        {"id": "FRA", "name": "Frankenbach", "x": 120, "y": 420, "lines": ["2"]},
        {"id": "SOL", "name": "Sontheim", "x": 280, "y": 150, "lines": ["3", "4"]},
        {"id": "NEU", "name": "Neckargartach", "x": 350, "y": 120, "lines": ["3"]},
        {"id": "BIB", "name": "Bildungscampus", "x": 380, "y": 300, "lines": ["4"]},
        {"id": "BOT", "name": "Botanischer Garten", "x": 450, "y": 320, "lines": ["4"]},
    ]

    edges = [
        {"from_id": "HBF", "to_id": "HT", "lines": ["1"]},
        {"from_id": "HT", "to_id": "RAT", "lines": ["1"]},
        {"from_id": "RAT", "to_id": "WOL", "lines": ["1"]},
        {"from_id": "WOL", "to_id": "OEH", "lines": ["1"]},
        {"from_id": "FRA", "to_id": "BOE", "lines": ["2"]},
        {"from_id": "BOE", "to_id": "KIL", "lines": ["2"]},
        {"from_id": "KIL", "to_id": "HBF", "lines": ["2"]},
        {"from_id": "HBF", "to_id": "HT", "lines": ["2"]},
        {"from_id": "HT", "to_id": "RAT", "lines": ["2"]},
        {"from_id": "NEU", "to_id": "SOL", "lines": ["3"]},
        {"from_id": "SOL", "to_id": "HBF", "lines": ["3"]},
        {"from_id": "HBF", "to_id": "RAT", "lines": ["3"]},
        {"from_id": "RAT", "to_id": "WOL", "lines": ["3"]},
        {"from_id": "SOL", "to_id": "HBF", "lines": ["4"]},
        {"from_id": "HBF", "to_id": "KIL", "lines": ["4"]},
        {"from_id": "KIL", "to_id": "BIB", "lines": ["4"]},
        {"from_id": "BIB", "to_id": "BOT", "lines": ["4"]},
    ]

    network_data = {
        "name": "Heilbronn Stadtbahn (Sample)",
        "stations": stations,
        "edges": edges,
    }

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(network_data, f, indent=2, ensure_ascii=False)

    print(f"Created sample Heilbronn network with {len(stations)} stations and {len(edges)} edges")
    print(f"Saved to {save_path}")

    return network_data


# -----------------------------
# GTFS loader
# -----------------------------

def load_gtfs_data(
    gtfs_folder: str,
    save_path: str = "heilbronn_network.json",
    merge_distance_m: float = 25.0,
    require_name_match: bool = False,
):
    """Load a simplified network from GTFS.

    - Collapses platform stops into their parent_station when available.
    - Optionally also deduplicates close-by stops (useful when parent_station is missing).
    """

    import pandas as pd

    print(f"Loading GTFS data from {gtfs_folder}...")

    try:
        stops_df = pd.read_csv(os.path.join(gtfs_folder, "stops.txt"), dtype=str)
        routes_df = pd.read_csv(os.path.join(gtfs_folder, "routes.txt"), dtype=str)
        trips_df = pd.read_csv(os.path.join(gtfs_folder, "trips.txt"), dtype=str)
        stop_times_df = pd.read_csv(os.path.join(gtfs_folder, "stop_times.txt"), dtype=str)

        # Ensure numeric fields exist
        for col in ("stop_lat", "stop_lon"):
            if col in stops_df.columns:
                stops_df[col] = stops_df[col].astype(float)

        # Filter for tram/light rail/metro if GTFS uses route_type
        tram_like = routes_df
        if "route_type" in routes_df.columns:
            tram_like = routes_df[routes_df["route_type"].isin(["0", "1", 0, 1])]

        # Index stops by stop_id
        stops_by_id = {row["stop_id"]: row for _, row in stops_df.iterrows() if "stop_id" in row}

        deduper = StopDeduper(merge_distance_m=merge_distance_m, require_name_match=require_name_match)
        edges: Dict[Tuple[str, str], set] = defaultdict(set)

        # Helper to get a canonical stop for a stop_id
        def canonical_stop_id(stop_id: str) -> Optional[str]:
            row = stops_by_id.get(stop_id)
            if row is None:
                return None

            parent = ""
            if "parent_station" in row and isinstance(row["parent_station"], str):
                parent = row["parent_station"].strip()

            # Prefer parent station as canonical
            cid = parent if parent else stop_id

            # Choose coordinates/name from the canonical station row if present
            base_row = stops_by_id.get(cid, row)
            name = str(base_row.get("stop_name", ""))
            lat = float(base_row.get("stop_lat", row.get("stop_lat", 0.0)))
            lon = float(base_row.get("stop_lon", row.get("stop_lon", 0.0)))

            # Run through deduper as an extra safety net
            return deduper.upsert(original_id=str(stop_id), name=name, lat=lat, lon=lon, prefer_canonical_id=cid)

        # Process each route
        for _, route in tram_like.iterrows():
            route_id = route.get("route_id")
            route_name = route.get("route_short_name") or route.get("route_long_name") or route_id or "Unknown"
            if not route_id:
                continue

            route_trips = trips_df[trips_df["route_id"] == route_id]
            if route_trips.empty:
                continue

            # Representative trip: take the first trip id
            trip_id = route_trips.iloc[0]["trip_id"]
            trip_stops = stop_times_df[stop_times_df["trip_id"] == trip_id]
            if trip_stops.empty:
                continue

            if "stop_sequence" in trip_stops.columns:
                trip_stops = trip_stops.sort_values("stop_sequence")

            seq: List[str] = []
            for _, st in trip_stops.iterrows():
                sid = st.get("stop_id")
                if not sid:
                    continue
                cid = canonical_stop_id(str(sid))
                if not cid:
                    continue
                deduper.add_line(cid, str(route_name))
                if not seq or seq[-1] != cid:
                    seq.append(cid)

            for i in range(len(seq) - 1):
                _add_edge(edges, seq[i], seq[i + 1], str(route_name))

        stations = deduper.stations_as_dict()
        _normalize_station_coordinates(stations)

        edges_list = [
            {"from_id": u, "to_id": v, "lines": sorted(list(lines))}
            for (u, v), lines in edges.items()
        ]

        network_data = {
            "name": "Heilbronn GTFS Network",
            "stations": list(stations.values()),
            "edges": edges_list,
        }

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(network_data, f, indent=2, ensure_ascii=False)

        print(f"Success! Saved {len(stations)} stations and {len(edges_list)} edges to {save_path}")
        return network_data

    except Exception as e:
        print(f"Error loading GTFS data: {e}")
        return None


# -----------------------------
# CLI
# -----------------------------

if __name__ == "__main__":
    print("Heilbronn Transport Network Data Loader (rewritten)")
    print("=" * 60)
    print("\nOptions:")
    print("1. Create sample Heilbronn network (recommended for testing)")
    print("2. Fetch from OpenStreetMap (requires internet + overpy)")
    print("3. Load from GTFS files")

    choice = input("\nEnter choice (1/2/3) [default: 1]: ").strip() or "1"

    if choice == "1":
        create_heilbronn_sample("heilbronn_network.json")

    elif choice == "2":
        try:
            md = input("Merge distance in meters [default: 25]: ").strip()
            merge_dist = float(md) if md else 25.0
            require_nm = (input("Require name match? (y/N): ").strip().lower() == "y")
            fetch_heilbronn_osm_data(
                save_path="heilbronn_network.json",
                merge_distance_m=merge_dist,
                require_name_match=require_nm,
            )
        except ImportError:
            print("Error: 'overpy' not installed. Install with: pip install overpy")

    elif choice == "3":
        gtfs_path = input("Enter path to GTFS folder: ").strip()
        md = input("Merge distance in meters [default: 25]: ").strip()
        merge_dist = float(md) if md else 25.0
        require_nm = (input("Require name match? (y/N): ").strip().lower() == "y")
        load_gtfs_data(
            gtfs_folder=gtfs_path,
            save_path="heilbronn_network.json",
            merge_distance_m=merge_dist,
            require_name_match=require_nm,
        )

    print("\n" + "=" * 60)
    print("Data preparation complete!")
