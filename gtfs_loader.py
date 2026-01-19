"""
GTFS Data Loader for Heilbronn Transport Network
Loads and processes GTFS data to create a transit network suitable for octilinear visualization
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional
from dataclasses import dataclass
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# Add pandas option to handle mixed types
pd.options.mode.chained_assignment = None

@dataclass
class Stop:
    """Represents a transit stop"""
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    stop_code: str = ""
    location_type: int = 0
    parent_station: str = ""
    
@dataclass
class Route:
    """Represents a transit route"""
    route_id: str
    route_short_name: str
    route_long_name: str
    route_type: int
    route_color: str = "808080"
    route_text_color: str = "FFFFFF"
    agency_id: str = ""

@dataclass
class Trip:
    """Represents a single trip"""
    trip_id: str
    route_id: str
    service_id: str
    trip_headsign: str = ""
    direction_id: int = 0
    shape_id: str = ""

class GTFSLoader:
    """Loads and processes GTFS data"""
    
    def __init__(self, gtfs_path: str):
        """
        Initialize GTFS loader
        
        Args:
            gtfs_path: Path to directory containing GTFS .txt files
        """
        self.gtfs_path = Path(gtfs_path)
        self.stops: Dict[str, Stop] = {}
        self.routes: Dict[str, Route] = {}
        self.trips: Dict[str, Trip] = {}
        self.stop_times_df = None
        self.shapes_df = None
        
    def load_all(self):
        """Load all GTFS files"""
        print("Loading GTFS data...")
        self.load_stops()
        self.load_routes()
        self.load_trips()
        self.load_stop_times()
        try:
            self.load_shapes()
        except:
            print("No shapes file found - will use stop sequences")
        print(f"✓ Loaded: {len(self.stops)} stops, {len(self.routes)} routes, {len(self.trips)} trips")
        
    def load_stops(self):
        """Load stops.txt"""
        stops_file = self.gtfs_path / "stops.txt"
        if not stops_file.exists():
            stops_file = self.gtfs_path / "stops"  # Try without extension
        
        try:
            df = pd.read_csv(stops_file, dtype=str)
            for _, row in df.iterrows():
                # Handle missing values
                location_type = row.get('location_type', '0')
                if pd.isna(location_type) or location_type == '':
                    location_type = 0
                else:
                    location_type = int(float(location_type))
                
                parent_station = row.get('parent_station', '')
                if pd.isna(parent_station):
                    parent_station = ''
                
                stop_code = row.get('stop_code', '')
                if pd.isna(stop_code):
                    stop_code = ''
                
                stop = Stop(
                    stop_id=row['stop_id'],
                    stop_name=row['stop_name'],
                    stop_lat=float(row['stop_lat']),
                    stop_lon=float(row['stop_lon']),
                    stop_code=stop_code,
                    location_type=location_type,
                    parent_station=parent_station
                )
                self.stops[stop.stop_id] = stop
            print(f"  ✓ Loaded {len(self.stops)} stops")
        except Exception as e:
            print(f"  ✗ Error loading stops: {e}")
            raise
            
    def load_routes(self):
        """Load routes.txt"""
        routes_file = self.gtfs_path / "routes.txt"
        if not routes_file.exists():
            routes_file = self.gtfs_path / "routes"
            
        try:
            df = pd.read_csv(routes_file, dtype=str)
            for _, row in df.iterrows():
                # Handle missing values
                route_short_name = row.get('route_short_name', '')
                if pd.isna(route_short_name):
                    route_short_name = ''
                
                route_long_name = row.get('route_long_name', '')
                if pd.isna(route_long_name):
                    route_long_name = ''
                
                route_color = row.get('route_color', '808080')
                if pd.isna(route_color) or route_color == '':
                    route_color = '808080'
                
                route_text_color = row.get('route_text_color', 'FFFFFF')
                if pd.isna(route_text_color) or route_text_color == '':
                    route_text_color = 'FFFFFF'
                
                agency_id = row.get('agency_id', '')
                if pd.isna(agency_id):
                    agency_id = ''
                
                route = Route(
                    route_id=row['route_id'],
                    route_short_name=route_short_name,
                    route_long_name=route_long_name,
                    route_type=int(float(row['route_type'])),
                    route_color=route_color,
                    route_text_color=route_text_color,
                    agency_id=agency_id
                )
                self.routes[route.route_id] = route
            print(f"  ✓ Loaded {len(self.routes)} routes")
        except Exception as e:
            print(f"  ✗ Error loading routes: {e}")
            raise
            
    def load_trips(self):
        """Load trips.txt"""
        trips_file = self.gtfs_path / "trips.txt"
        if not trips_file.exists():
            trips_file = self.gtfs_path / "trips"
            
        try:
            df = pd.read_csv(trips_file, dtype=str)
            for _, row in df.iterrows():
                # Handle missing values
                trip_headsign = row.get('trip_headsign', '')
                if pd.isna(trip_headsign):
                    trip_headsign = ''
                
                direction_id = row.get('direction_id', '0')
                if pd.isna(direction_id) or direction_id == '':
                    direction_id = 0
                else:
                    direction_id = int(float(direction_id))
                
                shape_id = row.get('shape_id', '')
                if pd.isna(shape_id):
                    shape_id = ''
                
                trip = Trip(
                    trip_id=row['trip_id'],
                    route_id=row['route_id'],
                    service_id=row['service_id'],
                    trip_headsign=trip_headsign,
                    direction_id=direction_id,
                    shape_id=shape_id
                )
                self.trips[trip.trip_id] = trip
            print(f"  ✓ Loaded {len(self.trips)} trips")
        except Exception as e:
            print(f"  ✗ Error loading trips: {e}")
            raise
            
    def load_stop_times(self):
        """Load stop_times.txt"""
        stop_times_file = self.gtfs_path / "stop_times.txt"
        if not stop_times_file.exists():
            stop_times_file = self.gtfs_path / "stop_times"
            
        try:
            # Only load necessary columns to save memory
            self.stop_times_df = pd.read_csv(
                stop_times_file,
                dtype={'trip_id': str, 'stop_id': str, 'stop_sequence': int},
                usecols=['trip_id', 'stop_id', 'stop_sequence', 'arrival_time', 'departure_time']
            )
            print(f"  ✓ Loaded {len(self.stop_times_df)} stop times")
        except Exception as e:
            print(f"  ✗ Error loading stop_times: {e}")
            raise
            
    def load_shapes(self):
        """Load shapes.txt (optional)"""
        shapes_file = self.gtfs_path / "shapes.txt"
        if not shapes_file.exists():
            shapes_file = self.gtfs_path / "shapes"
            
        if shapes_file.exists():
            try:
                self.shapes_df = pd.read_csv(
                    shapes_file,
                    dtype={'shape_id': str, 'shape_pt_sequence': int},
                    usecols=['shape_id', 'shape_pt_lat', 'shape_pt_lon', 'shape_pt_sequence']
                )
                print(f"  ✓ Loaded shapes data")
            except Exception as e:
                print(f"  ! Shapes file found but couldn't load: {e}")
        
    def get_available_routes(self) -> List[Tuple[str, str, str]]:
        """
        Get list of available routes for filtering
        
        Returns:
            List of (route_id, route_short_name, route_long_name) tuples
        """
        routes = []
        for route_id, route in self.routes.items():
            routes.append((
                route_id,
                route.route_short_name,
                route.route_long_name
            ))
        return sorted(routes, key=lambda x: x[1])  # Sort by short name
    
    def build_network(
        self,
        selected_route_ids: Optional[List[str]] = None,
        top_k_patterns: int = 2,
        min_pattern_freq: int = 1,
        max_trips_per_pattern: int = 50,
    ):
        """
        Build transit network from GTFS data

        Args:
            selected_route_ids: List of route IDs to include (None = all routes)
            top_k_patterns: Keep the K most frequent stop sequences per route (recommended: 1 or 2)
            min_pattern_freq: Ignore patterns that occur less than this many times
            max_trips_per_pattern: Limit number of trips used per kept pattern (speed/memory guard)

        Returns:
            Dictionary with 'nodes', 'edges', 'route_colors', 'routes'
        """
        print("\nBuilding transit network.")

        # ------------------------------------------------------------
        # 1) Select trips: ALL trips for selected routes (not just one)
        # ------------------------------------------------------------
        if selected_route_ids:
            selected_route_ids_set = set(selected_route_ids)
            selected_trips = {
                trip_id: trip
                for trip_id, trip in self.trips.items()
                if trip.route_id in selected_route_ids_set
            }
            routes_in_trips = {t.route_id for t in selected_trips.values()}
            print(f"  Selected {len(selected_trips)} trips from {len(routes_in_trips)} routes (ALL trips)")
        else:
            selected_trips = self.trips
            print(f"  Using all {len(selected_trips)} trips")

        if not selected_trips:
            print("  ! No trips selected - returning empty network")
            return {"nodes": [], "edges": [], "route_colors": {}, "routes": {}}

        # ------------------------------------------------------------
        # 2) FAST: Build stop sequences for all selected trips in ONE pass
        # ------------------------------------------------------------
        selected_trip_ids = set(selected_trips.keys())

        st = self.stop_times_df.loc[
            self.stop_times_df["trip_id"].isin(selected_trip_ids),
            ["trip_id", "stop_id", "stop_sequence"],
        ].copy()

        st = st.sort_values(["trip_id", "stop_sequence"])

        # trip_id -> [stop_id, ...]
        trip_stops = st.groupby("trip_id", sort=False)["stop_id"].apply(list).to_dict()
        trip_stops = {tid: stops for tid, stops in trip_stops.items() if len(stops) >= 2}

        # ------------------------------------------------------------
        # 3) Keep only top-K most frequent stop sequences per route
        # ------------------------------------------------------------
        route_pattern_trips = defaultdict(lambda: defaultdict(list))

        for trip_id, stops_list in trip_stops.items():
            route_id = selected_trips[trip_id].route_id
            pattern = tuple(stops_list)
            route_pattern_trips[route_id][pattern].append(trip_id)

        kept_trip_ids = set()

        for route_id, pattern_map in route_pattern_trips.items():
            patterns_sorted = sorted(
                pattern_map.items(),
                key=lambda kv: len(kv[1]),
                reverse=True
            )

            # Apply min frequency filter
            patterns_sorted = [(pat, tids) for (pat, tids) in patterns_sorted if len(tids) >= min_pattern_freq]

            # Take top-K patterns
            patterns_sorted = patterns_sorted[: max(1, int(top_k_patterns))]

            # Keep up to max_trips_per_pattern trips for each kept pattern
            for pat, tids in patterns_sorted:
                tids_sorted = sorted(tids)[: max(1, int(max_trips_per_pattern))]
                kept_trip_ids.update(tids_sorted)

        # Filter selected_trips + trip_stops down to the kept set
        selected_trips = {tid: selected_trips[tid] for tid in kept_trip_ids if tid in selected_trips}
        trip_stops = {tid: trip_stops[tid] for tid in kept_trip_ids if tid in trip_stops}

        print(
            f"  ✓ Pattern filter: kept {len(trip_stops)} trips "
            f"(top_k_patterns={top_k_patterns}, min_freq={min_pattern_freq})"
        )

        # ------------------------------------------------------------
        # 4) Collect all stops that are actually used
        # ------------------------------------------------------------
        used_stop_ids = set()
        for stops_list in trip_stops.values():
            used_stop_ids.update(stops_list)

        print(f"  Network uses {len(used_stop_ids)} stops")

        # ------------------------------------------------------------
        # 5) Build edges (connections between consecutive stops on routes)
        # ------------------------------------------------------------
        edges_dict = defaultdict(set)  # (stop1, stop2) -> set of route_ids

        for trip_id, stops_list in trip_stops.items():
            route_id = selected_trips[trip_id].route_id

            for i in range(len(stops_list) - 1):
                stop1 = stops_list[i]
                stop2 = stops_list[i + 1]

                if stop1 not in self.stops or stop2 not in self.stops:
                    continue

                edge_key = tuple(sorted([stop1, stop2]))
                edges_dict[edge_key].add(route_id)

        # ------------------------------------------------------------
        # 6) Convert to network format
        # ------------------------------------------------------------
        nodes = []
        for stop_id in used_stop_ids:
            stop = self.stops.get(stop_id)
            if stop is None:
                continue
            nodes.append({
                "id": stop_id,
                "name": stop.stop_name,
                "lat": stop.stop_lat,
                "lon": stop.stop_lon
            })

        edges = []
        for (stop1, stop2), route_ids in edges_dict.items():
            edges.append({
                "from": stop1,
                "to": stop2,
                "routes": list(route_ids)
            })
        
        routes_used = set()
        for route_ids in edges_dict.values():
            routes_used.update(route_ids)

        # Route colors
        route_colors = {}
        for route_id, route in self.routes.items():
            if selected_route_ids is None or route_id in set(selected_route_ids):
                color = f"#{route.route_color}" if not route.route_color.startswith("#") else route.route_color
                route_colors[route_id] = color

        routes_info = {}
        for rid in routes_used:
            route = self.routes.get(rid)
            if route is None:
                continue
            short = route.route_short_name
            if short is None or str(short).strip() == "":
                short = route.route_long_name or rid   # fallback
            routes_info[rid] = str(short).strip()

        print(f"  ✓ Built network: {len(nodes)} nodes, {len(edges)} edges")

        return {
            "nodes": nodes,
            "edges": edges,
            "route_colors": route_colors,
            "routes": routes_info
        }



def lat_lon_to_meters(lat, lon, ref_lat, ref_lon):
    """
    Convert lat/lon to approximate meters from reference point
    Uses simple equirectangular projection
    """
    R = 6371000  # Earth radius in meters
    
    x = (lon - ref_lon) * np.cos(np.radians(ref_lat)) * np.pi * R / 180
    y = (lat - ref_lat) * np.pi * R / 180
    
    return x, y

def meters_to_lat_lon(x, y, ref_lat, ref_lon):
    """Convert meters back to lat/lon"""
    R = 6371000
    
    lon = ref_lon + (x * 180) / (np.pi * R * np.cos(np.radians(ref_lat)))
    lat = ref_lat + (y * 180) / (np.pi * R)
    
    return lat, lon