"""
Octilinear Metro Map Generator for Heilbronn
Generates schematic transit maps from GTFS data using the approximation algorithm

+ Local optimization step after drawing (paper Sec. 4.6 style)
+ Progress logging through the optimization process (iteration-by-iteration)
+ Corrected bend penalties (favoring smoother/obtuse turns)
+ Fixed and fast degree-2 node reinsertion
+ FIXED: Capped A* expansions (prevents local search from hanging)
+ FIXED: Local search reroutes only actually-routed adjacent edges
+ FIXED: Local search skips degree-1 nodes (wasted work)
+ FIXED: Corrected generate() reporting (after optimization printed before reinsertion)
"""

import numpy as np
import math
import matplotlib.pyplot as plt
from typing import List, Dict, Tuple, Set, Optional
from dataclasses import dataclass
from collections import defaultdict
import heapq

from gtfs_loader import lat_lon_to_meters


# ----------------------------- Data classes -----------------------------

@dataclass
class Node:
    id: str
    name: str
    x: float  # meters
    y: float  # meters
    lat: float
    lon: float

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, Node) and self.id == other.id


@dataclass
class Edge:
    from_id: str
    to_id: str
    routes: List[str]

    def get_key(self):
        return tuple(sorted([self.from_id, self.to_id]))


@dataclass
class GridNode:
    gx: int
    gy: int
    x: float
    y: float

    def __hash__(self):
        return hash((self.gx, self.gy))

    def __eq__(self, other):
        return isinstance(other, GridNode) and self.gx == other.gx and self.gy == other.gy

    def __lt__(self, other):
        return (self.gx, self.gy) < (other.gx, other.gy)


# --------------------------- Main generator -----------------------------

class OctilinearMapGenerator:
    """Generates octilinear metro maps from transit network data"""

    def __init__(
        self,
        network_data: Dict,
        grid_size: float = 200.0,
        grid_padding_cells: float = 3.0,
        candidate_radius_cells: float = 3.0,
        min_station_distance_m: float = 0.0,
    ):
        """
        Args:
            network_data: Output of GTFSLoader.build_network()
            grid_size: Grid cell size in meters
            grid_padding_cells: Extra padding (in cells) around the bbox
            candidate_radius_cells: Candidate placement radius (in cells) around each stop's geo position
            min_station_distance_m: Minimum distance (in meters) allowed between any two placed stations (0 disables)
        """
        self.network_data = network_data
        self.grid_size = grid_size
        self.grid_padding_cells = grid_padding_cells
        self.candidate_radius_cells = candidate_radius_cells
        self.min_station_distance_m = float(min_station_distance_m)
        # Minimum distance as Chebyshev radius in grid cells (>=0).
        # Using Chebyshev keeps a square exclusion zone which is fast and stable on a grid.
        self.min_station_distance_cells = int(np.ceil(self.min_station_distance_m / self.grid_size)) if self.min_station_distance_m > 0 else 0

        # Original nodes/edges (for geo/reference)
        self.nodes: Dict[str, Node] = {}
        self.edges: List[Edge] = []

        # Working graph (after degree-2 contraction)
        self.work_nodes: Set[str] = set()
        self.work_edges: List[Edge] = []
        self.contracted_chains: Dict[Tuple[str, str, str], List[str]] = {}
        #    (route_id, u, v) -> [contracted node ids]



        self.route_colors: Dict[str, Tuple[float, float, float]] = self._assign_route_colors()
        self.used_edges: Set[Tuple[GridNode, GridNode]] = set()

        # Algorithm state
        self.line_degrees: Dict[str, Tuple[int, int]] = {}
        self.node_order: List[str] = []
        self.edge_order: List[Edge] = []
        self.grid_nodes: List[GridNode] = []
        self.node_candidates: Dict[str, List[GridNode]] = {}

        self.settled_nodes: Dict[str, GridNode] = {}
        self.routed_paths: List[Tuple[Edge, List[GridNode]]] = []

        # Hard obstacles
        self.blocked_nodes: Set[GridNode] = set()
        self.blocked_edges: Set[Tuple[GridNode, GridNode]] = set()

        # Corridor sharing: segments and nodes that may be reused by later edges
        self.shareable_edges: Set[Tuple[GridNode, GridNode]] = set()
        self.shareable_nodes: Set[GridNode] = set()


        # Fast grid lookup
        self.grid_index: Dict[Tuple[int, int], GridNode] = {}

        # Build node/edge structures
        self._prepare_network()

        # Contract degree-2 nodes once
        self._contract_degree2_graph()
        

        self.rotation_system = {}
        self.node_ports = defaultdict(dict)

     

    # ---------------------- Network / colors ----------------------

    def _angle_geo(self, u: str, v: str) -> float:
        """Angle at u pointing to v, in [0, 2pi). Uses original geo coordinates."""
        a = self.nodes[u]
        b = self.nodes[v]
        ang = math.atan2(b.y - a.y, b.x - a.x)
        if ang < 0:
            ang += 2 * math.pi
        return ang

    def compute_rotation_system(self) -> Dict[str, List[str]]:
        """For each node u, return neighbors sorted CCW by geo angle."""
        rot = {}
        for u in self.work_nodes:
            nbrs = self.get_neighbors(u)
            nbrs.sort(key=lambda v: self._angle_geo(u, v))
            rot[u] = nbrs
        return rot

# ... inside class OctilinearMapGenerator:

    _DIR_ANGLES = [  # must match your _DIRS_8 order!
        225, 270, 315,
        180,      0,
        135,  90,  45
    ]

    def _dir_angle_rad(self, dir_idx: int) -> float:
        return (math.radians(self._DIR_ANGLES[dir_idx]) + 2 * math.pi) % (2 * math.pi)


    def _angdiff(self, a: float, b: float) -> float:
        """Smallest absolute angular difference between angles a and b (radians)."""
        d = (a - b) % (2 * math.pi)
        if d > math.pi:
            d = 2 * math.pi - d
        return abs(d)

    def _angle_geo(self, u: str, v: str) -> float:
        """Angle at u pointing to v, in [0, 2pi). Uses original geo coordinates."""
        a = self.nodes[u]
        b = self.nodes[v]
        ang = math.atan2(b.y - a.y, b.x - a.x)
        if ang < 0:
            ang += 2 * math.pi
        return ang

    def _assign_ports_for_node(self, u: str):
        """Assign a dir_index (0..7) for each neighbor of u, preserving cyclic order.

        Used to preserve topology by constraining the first/last grid step direction ("ports")
        for edges incident to already-settled stations.
        """
        nbrs = self.rotation_system.get(u, [])
        d = len(nbrs)
        if d == 0:
            return

        # degree > 8: allow multiple edges per port (bundling) but keep stable order
        if d > 8:
            for v in nbrs:
                theta = self._angle_geo(u, v)
                best = min(range(8), key=lambda k: self._angdiff(theta, self._dir_angle_rad(k)))
                self.node_ports[u][v] = best
            return

        # degree <= 8: choose rotation offset that best matches angles while preserving cyclic order
        thetas = [self._angle_geo(u, v) for v in nbrs]
        phis = [self._dir_angle_rad(k) for k in range(8)]

        best_offset = 0
        best_cost = float('inf')
        for off in range(8):
            cost = 0.0
            for i in range(d):
                cost += self._angdiff(thetas[i], phis[(off + i) % 8])
            if cost < best_cost:
                best_cost = cost
                best_offset = off

        for i, v in enumerate(nbrs):
            self.node_ports[u][v] = (best_offset + i) % 8


    def _assign_route_colors(self) -> Dict[str, Tuple[float, float, float]]:
        """Assign a unique, stable color to each route."""
        palette = plt.cm.tab20.colors
        route_colors = {}
        for i, route_id in enumerate(sorted(self.network_data.get("routes", []))):
            route_colors[route_id] = palette[i % len(palette)]
        return route_colors

    def _prepare_network(self):
        """Convert network data to internal format with metric coordinates."""
        lats = [n["lat"] for n in self.network_data["nodes"]]
        lons = [n["lon"] for n in self.network_data["nodes"]]
        ref_lat = float(np.mean(lats))
        ref_lon = float(np.mean(lons))

        print(f"\nPreparing network...")
        print(f"  Reference point: {ref_lat:.6f}, {ref_lon:.6f}")

        for nd in self.network_data["nodes"]:
            x, y = lat_lon_to_meters(nd["lat"], nd["lon"], ref_lat, ref_lon)
            self.nodes[nd["id"]] = Node(
                id=nd["id"],
                name=nd["name"],
                x=x, y=y,
                lat=nd["lat"],
                lon=nd["lon"]
            )

        for ed in self.network_data["edges"]:
            self.edges.append(Edge(
                from_id=ed["from"],
                to_id=ed["to"],
                routes=list(ed["routes"])
            ))

        print(f"  ✓ Network prepared: {len(self.nodes)} nodes, {len(self.edges)} edges")

    def _edge_key_nodes(self, a: str, b: str) -> Tuple[str, str]:
        return tuple(sorted([a, b]))

    def _build_adj(self, edges: List[Edge]) -> Dict[str, Set[str]]:
        adj: Dict[str, Set[str]] = defaultdict(set)
        for e in edges:
            adj[e.from_id].add(e.to_id)
            adj[e.to_id].add(e.from_id)
        return adj

    def _contract_degree2_graph(self):
        """Contract degree-2 nodes on the *global* network graph (after building the network).

        A node v with exactly two neighbors a,b is contracted ONLY if the route-set on edge (a,v)
        equals the route-set on edge (v,b). This prevents contracting route-junction/terminus nodes
        that are degree-2 geometrically but where the set of services changes.

        We store contracted intermediate nodes per contracted edge (undirected key), oriented from
        key[0] -> key[1]. Reinsertion later places those intermediate stations back on the routed polyline.
        """
        # Build undirected edge -> route-set, and adjacency
        edge_routes = {}  # (u,v) sorted -> set(routes)
        adj = defaultdict(set)
        for e in self.edges:
            k = self._edge_key_nodes(e.from_id, e.to_id)
            edge_routes.setdefault(k, set()).update(e.routes or [])
            adj[e.from_id].add(e.to_id)
            adj[e.to_id].add(e.from_id)

        contracted_chains = {}  # (u,v) sorted -> list of intermediate node ids oriented u->v

        def get_chain(src: str, dst: str):
            k = self._edge_key_nodes(src, dst)
            ch = contracted_chains.get(k, [])
            return list(ch) if (src, dst) == k else list(reversed(ch))

        changed = True
        alive = set(adj.keys())
        while changed:
            changed = False
            for v in list(alive):
                if v not in adj:
                    alive.discard(v)
                    continue
                neigh = list(adj[v])
                if len(neigh) != 2:
                    continue
                a, b = neigh[0], neigh[1]
                if a == b:
                    continue

                k_av = self._edge_key_nodes(a, v)
                k_vb = self._edge_key_nodes(v, b)
                if k_av not in edge_routes or k_vb not in edge_routes:
                    continue

                # Only contract if the service sets match across the node.
                if edge_routes[k_av] != edge_routes[k_vb]:
                    continue

                k_ab = self._edge_key_nodes(a, b)
                # Avoid creating a parallel/shortcut edge in cycles; keep topology.
                if k_ab in edge_routes:
                    continue

                routes_set = set(edge_routes[k_av])

                chain_a_v = get_chain(a, v)
                chain_v_b = get_chain(v, b)
                merged = chain_a_v + [v] + chain_v_b  # oriented a->b

                # Remove old edges
                edge_routes.pop(k_av, None)
                edge_routes.pop(k_vb, None)
                contracted_chains.pop(k_av, None)
                contracted_chains.pop(k_vb, None)

                # Update adjacency
                adj[a].discard(v)
                adj[b].discard(v)
                adj.pop(v, None)
                alive.discard(v)
                adj[a].add(b)
                adj[b].add(a)

                # Add new edge
                edge_routes[k_ab] = routes_set

                # Store chain oriented key[0]->key[1]
                if (a, b) == k_ab:
                    contracted_chains[k_ab] = merged
                else:
                    contracted_chains[k_ab] = list(reversed(merged))

                changed = True

        self.work_edges = [Edge(u, v, sorted(list(rs))) for (u, v), rs in edge_routes.items()]
        self.work_nodes = set(adj.keys())
        self.contracted_chains = contracted_chains

        print(
            f"  ✓ Deg-2 contraction (global): {len(self.work_nodes)} work nodes, {len(self.work_edges)} work edges "
            f"(contracted {len(self.nodes) - len(self.work_nodes)} nodes in routing graph)"
        )

    # ---------------------- Node/edge ordering ----------------------

    def get_neighbors(self, node_id: str) -> List[str]:
        neighbors = []
        for edge in self.work_edges:
            if edge.from_id == node_id:
                neighbors.append(edge.to_id)
            elif edge.to_id == node_id:
                neighbors.append(edge.from_id)
        return neighbors

    def get_adjacent_edges(self, node_id: str) -> List[Edge]:
        return [e for e in self.work_edges if e.from_id == node_id or e.to_id == node_id]

    def calculate_line_degrees(self):
        self.line_degrees.clear()
        for node_id in self.work_nodes:
            adjacent_edges = self.get_adjacent_edges(node_id)
            total_lines = sum(len(edge.routes) for edge in adjacent_edges)
            degree = len(adjacent_edges)
            self.line_degrees[node_id] = (total_lines, degree)
        print(f"  ✓ Calculated line degrees")

    def compute_node_order(self):
        self.node_order = []
        processed = set()
        dangling = []

        # process ALL components, not just the one containing sorted_nodes[0]
        while len(processed) < len(self.work_nodes):
            if not dangling:
                # pick next unprocessed node with highest line-degree
                remaining = [n for n in self.work_nodes if n not in processed]
                remaining.sort(key=lambda nid: self.line_degrees[nid][0], reverse=True)
                dangling.append(remaining[0])

            dangling.sort(key=lambda nid: self.line_degrees[nid][0], reverse=True)
            current = dangling.pop(0)

            if current in processed:
                continue

            self.node_order.append(current)
            processed.add(current)

            for neighbor in self.get_neighbors(current):
                if neighbor not in processed:
                    dangling.append(neighbor)


        print("  ✓ Computed node order (all components)")


    def compute_edge_order(self):
        self.edge_order = []
        processed = set()

        for node_id in self.node_order:
            adjacent_edges = self.get_adjacent_edges(node_id)
            unprocessed = [e for e in adjacent_edges if e.get_key() not in processed]

            def other_degree(edge: Edge):
                other_id = edge.to_id if edge.from_id == node_id else edge.from_id
                return self.line_degrees.get(other_id, (0, 0))[0]

            unprocessed.sort(key=other_degree, reverse=True)

            for edge in unprocessed:
                if edge.get_key() not in processed:
                    self.edge_order.append(edge)
                    processed.add(edge.get_key())

        print(f"  ✓ Computed edge order: {len(self.edge_order)} edges")

    # ---------------------- Grid + candidates ----------------------

    def setup_grid(self):
        xs = [n.x for n in self.nodes.values()]
        ys = [n.y for n in self.nodes.values()]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)

        padding = self.grid_size * float(self.grid_padding_cells)
        x_min -= padding
        x_max += padding
        y_min -= padding
        y_max += padding

        cols = int((x_max - x_min) / self.grid_size) + 1
        rows = int((y_max - y_min) / self.grid_size) + 1

        self.grid_nodes = []
        self.grid_index = {}
        for gx in range(cols):
            for gy in range(rows):
                x = x_min + gx * self.grid_size
                y = y_min + gy * self.grid_size
                gn = GridNode(gx, gy, x, y)
                self.grid_nodes.append(gn)
                self.grid_index[(gx, gy)] = gn
        radius = float(self.candidate_radius_cells) * self.grid_size
        self.node_candidates = {}
        for node_id, node in self.nodes.items():
            candidates = []
            for grid_node in self.grid_nodes:
                dist = np.hypot(grid_node.x - node.x, grid_node.y - node.y)
                if dist < radius:
                    candidates.append(grid_node)
            self.node_candidates[node_id] = candidates

        avg_cand = float(np.mean([len(v) for v in self.node_candidates.values()])) if self.node_candidates else 0.0
        print(f"  ✓ Setup grid: {cols}x{rows} = {len(self.grid_nodes)} nodes | avg candidates ≈ {avg_cand:.1f}")

    # ---------------------- Objective / reporting ----------------------

    def _bend_count(self, path: List[GridNode]) -> int:
        if len(path) < 3:
            return 0
        b = 0
        for i in range(1, len(path) - 1):
            p0, p1, p2 = path[i - 1], path[i], path[i + 1]
            d1 = (p1.gx - p0.gx, p1.gy - p0.gy)
            d2 = (p2.gx - p1.gx, p2.gy - p1.gy)
            if d1 != d2:
                b += 1
        return b

    def compute_objective(self, routed_paths: Optional[List[Tuple[Edge, List[GridNode]]]] = None) -> Dict[str, float]:
        if routed_paths is None:
            routed_paths = self.routed_paths

        hops = 0
        bends = 0
        for _, path in routed_paths:
            hops += max(0, len(path) - 1)
            bends += self._bend_count(path)

        bend_w = 1.5
        total = float(hops) + bend_w * float(bends)
        return {"hops": float(hops), "bends": float(bends), "total": total}

    # ---------------------- Routing primitives ----------------------

    _DIRS_8 = [
        (-1, -1), (0, -1), (1, -1),
        (-1, 0),           (1, 0),
        (-1, 1),  (0, 1),  (1, 1)
    ]

    def _edge_key(self, a: GridNode, b: GridNode) -> Tuple[GridNode, GridNode]:
        return (a, b) if (a.gx, a.gy) <= (b.gx, b.gy) else (b, a)

    def _dir_index(self, dx: int, dy: int) -> int:
        for i, (ddx, ddy) in enumerate(self._DIRS_8):
            if dx == ddx and dy == ddy:
                return i
        raise ValueError(f"Invalid direction ({dx},{dy})")

    def _bend_cost(self, prev_dir: Optional[int], new_dir: int) -> float:
        """
        Bend cost with "smooth obtuse preferred" interpretation:
        d=1 or 7: 45° deviation -> internal angle 135° (smooth) => low cost
        d=2 or 6: 90° turn => medium cost
        d=3 or 5: 135° deviation -> internal angle 45° (sharp) => high cost
        d=4: reversal => very high (and also forbidden below)
        """
        if prev_dir is None:
            return 0.0
        d = (new_dir - prev_dir) % 8
        if d == 0:
            return 0.0
        if d in (1, 7):
            return 1.0
        if d in (2, 6):
            return 1.5
        if d in (3, 5):
            return 2.0
        return 10.0

    def _is_diagonal_step(self, dx: int, dy: int) -> bool:
        return abs(dx) == 1 and abs(dy) == 1

    # ---------------------- Station placement constraints ----------------------

    def _is_far_enough_from_settled(self, cand: GridNode, ignore_node_id: Optional[str] = None) -> bool:
        """Check min station spacing against already-settled stations (fast grid metric).

        We use Chebyshev distance in grid-cells so the excluded area is a square
        (stable + cheap on a grid).
        """
        if self.min_station_distance_cells <= 0:
            return True
        for nid, gn in self.settled_nodes.items():
            if ignore_node_id is not None and nid == ignore_node_id:
                continue
            dx = abs(cand.gx - gn.gx)
            dy = abs(cand.gy - gn.gy)
            if max(dx, dy) < self.min_station_distance_cells:
                return False
        return True

    def _filter_candidates_by_min_dist(self, cands: List[GridNode], ignore_node_id: Optional[str] = None) -> List[GridNode]:
        """Filter candidate grid nodes to satisfy the minimum station spacing.

        If filtering empties the candidate set, the caller can decide whether to fall back
        to the unfiltered set (soft constraint).
        """
        if not cands or self.min_station_distance_cells <= 0 or not self.settled_nodes:
            return cands
        return [c for c in cands if self._is_far_enough_from_settled(c, ignore_node_id=ignore_node_id)]

    def _split_candidates_voronoi(
        self,
        start_node_id: str,
        end_node_id: str,
        S: List[GridNode],
        T: List[GridNode],
    ) -> Tuple[List[GridNode], List[GridNode]]:
        if start_node_id in self.settled_nodes or end_node_id in self.settled_nodes:
            return S, T

        s_geo = self.nodes[start_node_id]
        t_geo = self.nodes[end_node_id]

        new_S: List[GridNode] = []
        new_T: List[GridNode] = []
        all_candidates = list(dict.fromkeys(S + T))

        for c in all_candidates:
            ds = (c.x - s_geo.x) ** 2 + (c.y - s_geo.y) ** 2
            dt = (c.x - t_geo.x) ** 2 + (c.y - t_geo.y) ** 2
            if ds <= dt:
                new_S.append(c)
            else:
                new_T.append(c)

        if not new_S:
            new_S = S
        if not new_T:
            new_T = T
        return new_S, new_T

    def _move_cost(self, node_id: str, cand: GridNode, cm: float = 0.5, ch: float = 1.0) -> float:
        n = self.nodes[node_id]
        dist = np.hypot(cand.x - n.x, cand.y - n.y)
        return (dist / self.grid_size) * (ch + cm)

    def _heuristic_to_targets(self, gn: GridNode, targets: List[GridNode]) -> float:
        if not targets:
            return 0.0
        return min(max(abs(gn.gx - t.gx), abs(gn.gy - t.gy)) for t in targets)

    def _diagonal_crossing_edge(self, a: GridNode, b: GridNode) -> Optional[Tuple[GridNode, GridNode]]:
        dx = b.gx - a.gx
        dy = b.gy - a.gy
        if abs(dx) != 1 or abs(dy) != 1:
            return None

        ll_gx = min(a.gx, b.gx)
        ll_gy = min(a.gy, b.gy)
        p1 = self.grid_index.get((ll_gx, ll_gy + 1))
        p2 = self.grid_index.get((ll_gx + 1, ll_gy))
        if p1 is None or p2 is None:
            return None
        return self._edge_key(p1, p2)

    def find_octilinear_path_set_to_set(
        self,
        start_node_id: str,
        end_node_id: str,
        start_candidates: List[GridNode],
        end_candidates: List[GridNode],
        max_expansions: int = 250000,  # FIX: cap to avoid hangs
    ) -> Tuple[Optional[List[GridNode]], Optional[GridNode], Optional[GridNode]]:
        """Set-to-set A* with bend penalties and hard obstacles (capped expansions)."""
        start_candidates = list(dict.fromkeys(start_candidates))
        end_candidates = list(dict.fromkeys(end_candidates))
        if not start_candidates or not end_candidates:
            return None, None, None

        end_set = set(end_candidates)

        def h(gn: GridNode) -> float:
            return self._heuristic_to_targets(gn, end_candidates)

        open_heap: List[Tuple[float, float, GridNode, Optional[int]]] = []
        came_from: Dict[Tuple[GridNode, Optional[int]], Tuple[Tuple[GridNode, Optional[int]], GridNode]] = {}
        g_score: Dict[Tuple[GridNode, Optional[int]], float] = {}

        for s in start_candidates:
            init = self._move_cost(start_node_id, s)
            state = (s, None)
            g_score[state] = init
            heapq.heappush(open_heap, (init + h(s), init, s, None))

        best_goal_state: Optional[Tuple[GridNode, Optional[int]]] = None
        best_goal_cost = float("inf")

        expansions = 0
        while open_heap:
            expansions += 1
            if expansions > max_expansions:
                break

            f, g, current, prev_dir = heapq.heappop(open_heap)
            state = (current, prev_dir)
            if g != g_score.get(state, None):
                continue

            if current in end_set:
                total = g + self._move_cost(end_node_id, current)
                if total < best_goal_cost:
                    best_goal_cost = total
                    best_goal_state = state
                if open_heap and open_heap[0][0] >= best_goal_cost:
                    break

            for dx, dy in self._DIRS_8:
                ng = self.grid_index.get((current.gx + dx, current.gy + dy))
                if ng is None:
                    continue
                ekey = self._edge_key(current, ng)

                # If a node is blocked, we only allow stepping onto it when traversing a shareable corridor edge.
                if ng in self.blocked_nodes and ng not in end_set:
                    if ekey not in self.shareable_edges:
                        continue

                # If an edge is blocked, we only allow it when it is explicitly shareable (corridor reuse).
                if ekey in self.blocked_edges and ekey not in self.shareable_edges:
                    continue

                cross = self._diagonal_crossing_edge(current, ng)
                if cross is not None and cross in self.blocked_edges:
                    continue

                new_dir = self._dir_index(dx, dy)

                # forbid immediate reversal (U-turn)
                if prev_dir is not None and ((new_dir - prev_dir) % 8) == 4:
                    continue

                hop = 1.0 + (0.5 if self._is_diagonal_step(dx, dy) else 0.0)
                step_cost = hop + self._bend_cost(prev_dir, new_dir)
                # Encourage bundling: if this segment is already used, slightly reduce cost
                reuse_bonus = 0.50  # tune 0.10 .. 0.50
                if ekey in self.used_edges:
                    step_cost -= reuse_bonus


                new_g = g + step_cost
                nstate = (ng, new_dir)
                if new_g < g_score.get(nstate, float("inf")):
                    g_score[nstate] = new_g
                    came_from[nstate] = (state, ng)
                    heapq.heappush(open_heap, (new_g + h(ng), new_g, ng, new_dir))

        if best_goal_state is None:
            return None, None, None  # FIX: fail cleanly

        path_nodes: List[GridNode] = []
        cur_state = best_goal_state
        cur_node, _ = cur_state
        path_nodes.append(cur_node)

        while cur_state in came_from:
            prev_state, _ = came_from[cur_state]
            prev_node, _ = prev_state
            path_nodes.append(prev_node)
            cur_state = prev_state

        path_nodes.reverse()
        return path_nodes, path_nodes[0], path_nodes[-1]

    # ---------------------- Edge routing helpers ----------------------

    def _neighbor_in_dir(self, gn: GridNode, dir_idx: int) -> Optional[GridNode]:
        dx, dy = self._DIRS_8[dir_idx]
        return self.grid_index.get((gn.gx + dx, gn.gy + dy))

    def _route_single_edge(self, edge: Edge, max_expansions: int = 250000) -> Optional[List[GridNode]]:
        """Route one edge with capped A* (safe for local search).
        Enforces topology by constraining the first/last step direction (ports) at settled nodes.
        """

        u = edge.from_id
        v = edge.to_id

        # --- Ensure ports exist for already-settled nodes (if you haven't assigned yet) ---
        if u in self.settled_nodes and (u not in self.node_ports or not self.node_ports[u]):
            self._assign_ports_for_node(u)
        if v in self.settled_nodes and (v not in self.node_ports or not self.node_ports[v]):
            self._assign_ports_for_node(v)

        # --- Build candidate sets S (start) and T (target), with port enforcement if possible ---
        if u in self.settled_nodes:
            u_gn = self.settled_nodes[u]
            dir_idx = self.node_ports.get(u, {}).get(v, None)
            if dir_idx is not None:
                forced = self._neighbor_in_dir(u_gn, dir_idx)
                S = [forced] if forced is not None else [u_gn]
            else:
                S = [u_gn]
        else:
            S = self.node_candidates.get(u, [])

        if v in self.settled_nodes:
            v_gn = self.settled_nodes[v]
            dir_idx = self.node_ports.get(v, {}).get(u, None)
            if dir_idx is not None:
                forced = self._neighbor_in_dir(v_gn, dir_idx)
                T = [forced] if forced is not None else [v_gn]
            else:
                T = [v_gn]
        else:
            T = self.node_candidates.get(v, [])

        if not S or not T:
            return None

        # Keep your existing pruning
        S, T = self._split_candidates_voronoi(u, v, S, T)

        # Enforce minimum station distance (only for nodes not yet settled).
        if u not in self.settled_nodes:
            S_f = self._filter_candidates_by_min_dist(S)
            if S_f:
                S = S_f
        if v not in self.settled_nodes:
            T_f = self._filter_candidates_by_min_dist(T)
            if T_f:
                T = T_f

        path, chosen_start, chosen_end = self.find_octilinear_path_set_to_set(
            u, v, S, T, max_expansions=max_expansions
        )
        if path is None or len(path) < 2:
            return None

        # --- Settle nodes if they were not settled yet ---
        if u not in self.settled_nodes and chosen_start is not None:
            self.settled_nodes[u] = chosen_start
            self._assign_ports_for_node(u)

        if v not in self.settled_nodes and chosen_end is not None:
            self.settled_nodes[v] = chosen_end
            self._assign_ports_for_node(v)

        # --- Stitch station nodes back into the path if we routed from/to a forced neighbor ---
        # (Important when u or v is settled: A* may start/end at the neighbor cell, not the station cell.)
        if u in self.settled_nodes:
            u_gn = self.settled_nodes[u]
            if path[0] != u_gn:
                path = [u_gn] + path

        if v in self.settled_nodes:
            v_gn = self.settled_nodes[v]
            if path[-1] != v_gn:
                path = path + [v_gn]

        return path


    def _apply_path_as_obstacle(self, path: List[GridNode], allow_edge_sharing: bool = False):
        """Paper-style obstacle closing.

        By default we block interior nodes and used edges so later routes do not overlap.
        If allow_edge_sharing=True (true shared-corridor edge), we still block the corridor
        nodes/edges for *crossing*, but mark the exact corridor edges as shareable so later
        routes may reuse them without detouring.
        """
        # block interior nodes
        for gn in path[1:-1]:
            self.blocked_nodes.add(gn)
            if allow_edge_sharing:
                self.shareable_nodes.add(gn)

        for j in range(len(path) - 1):
            a, b = path[j], path[j + 1]
            ekey = self._edge_key(a, b)

            # record usage (for reuse bonus)
            self.used_edges.add(ekey)

            # block edge generally; shareable edges are later allowed in A*
            self.blocked_edges.add(ekey)
            if allow_edge_sharing:
                self.shareable_edges.add(ekey)

            # always prevent diagonal X-crossings
            cross = self._diagonal_crossing_edge(a, b)
            if cross is not None:
                self.blocked_edges.add(cross)



    def route_all_edges(self):
        print(f"\nRouting {len(self.edge_order)} edges...")

        self.routed_paths = []
        self.blocked_nodes = set()
        self.blocked_edges = set()
        self.used_edges = set()
        self.shareable_edges = set()
        self.shareable_nodes = set()

        for i, edge in enumerate(self.edge_order):
            if (i + 1) % 50 == 0:
                print(f"  Progress: {i+1}/{len(self.edge_order)}")

            path = self._route_single_edge(edge, max_expansions=250000)
            if path is None:
                continue

            # Allow sharing ONLY for true shared-corridor edges (multi-route edge)
            allow_share = (len(edge.routes) > 1)

            # IMPORTANT: this requires _apply_path_as_obstacle(path, allow_edge_sharing=...)
            self._apply_path_as_obstacle(path, allow_edge_sharing=allow_share)

            self.routed_paths.append((edge, path))

        print(f"  ✓ All edges routed")

    # ---------------------- Reinsertion (deg-2) ----------------------

    def _reinsert_contracted_degree2_nodes(self):
        """Reinsert contracted degree-2 nodes along the routed polyline.

        Contracted chains are stored per *work edge* (undirected key) and are safe because
        contraction only happens when the route-set is identical on both incident edges.
        """
        if not self.contracted_chains:
            print("  ℹ No contracted degree-2 nodes to reinsert")
            return

        inserted = 0
        skipped_short = 0

        for edge, path in self.routed_paths:
            if len(path) < 2:
                continue

            k = self._edge_key_nodes(edge.from_id, edge.to_id)
            chain = self.contracted_chains.get(k)
            if not chain:
                continue

            # orient chain from edge.from_id -> edge.to_id
            if (edge.from_id, edge.to_id) != k:
                chain = list(reversed(chain))

            # Avoid re-inserting the same station if it appears in multiple chains (rare but possible in cycles)
            chain = [nid for nid in chain if nid not in self.settled_nodes]
            if not chain:
                continue

            klen = len(chain)
            L = len(path)
            if L < klen + 2:
                skipped_short += 1
                continue

            step = (L - 1) / (klen + 1)
            idx_prev = 0
            for i, node_id in enumerate(chain, start=1):
                idx = int(round(i * step))
                idx = min(max(1, idx), L - 2)
                idx = max(idx, idx_prev + 1)
                if idx >= L - 1:
                    break
                self.settled_nodes[node_id] = path[idx]
                inserted += 1
                idx_prev = idx

        print(f"  ✓ Reinserted {inserted} contracted degree-2 nodes (skipped_short_edges={skipped_short})")


    # ---------------------- Local Optimization (process logging) ----------------------

    def optimize_local_search(self, max_iters: int = 3, verbose: bool = True):
        """
        Local search with process logging (iteration-by-iteration):
        - find best improving move each iteration
        - apply it
        - print improvement stats
        """
        if not self.routed_paths or not self.settled_nodes:
            if verbose:
                print("  ! Local search skipped (nothing routed/settled yet).")
            before = self.compute_objective()
            return {"history": [], "before": before, "after": before}

        # FIX: optimize only useful nodes (degree >= 2)
        settled_ids = [
            nid for nid in self.settled_nodes.keys()
            if nid in self.work_nodes and self.line_degrees.get(nid, (0, 0))[1] >= 2
        ]

        def neighbors8(gn: GridNode):
            dirs = [(-1, -1), (-1, 0), (-1, 1),
                    (0, -1),           (0, 1),
                    (1, -1),  (1, 0),  (1, 1)]
            out = []
            for dx, dy in dirs:
                key = (gn.gx + dx, gn.gy + dy)
                if key in self.grid_index:
                    out.append(self.grid_index[key])
            return out

        def rebuild_obstacles_from_paths(paths: List[Tuple[Edge, List[GridNode]]]):
            blocked_nodes: Set[GridNode] = set()
            blocked_edges: Set[Tuple[GridNode, GridNode]] = set()
            shareable_nodes: Set[GridNode] = set()
            shareable_edges: Set[Tuple[GridNode, GridNode]] = set()

            endpoints = set(self.settled_nodes.values())

            for edge, path in paths:
                allow_share = (len(getattr(edge, 'routes', []) or []) > 1)

                for gn in path[1:-1]:
                    if gn not in endpoints:
                        blocked_nodes.add(gn)
                        if allow_share:
                            shareable_nodes.add(gn)

                for a, b in zip(path[:-1], path[1:]):
                    ekey = self._edge_key(a, b)
                    blocked_edges.add(ekey)
                    if allow_share:
                        shareable_edges.add(ekey)
                    cross = self._diagonal_crossing_edge(a, b)
                    if cross is not None:
                        blocked_edges.add(cross)

            return blocked_nodes, blocked_edges, shareable_nodes, shareable_edges

        before = self.compute_objective()
        best_global = before["total"]
        history = []

        if verbose:
            print(f"\nOptimizing with local search (max {max_iters} iterations)...")
            print(f"  Start: total={before['total']:.2f}, hops={before['hops']:.0f}, bends={before['bends']:.0f}")

        for it in range(1, max_iters + 1):
            best_move = None
            best_paths = None
            best_score = best_global
            best_move_desc = None

            for nid in settled_ids:
                current_gn = self.settled_nodes[nid]

                # FIX: reroute only edges that are currently routed
                adj_edges = [e for (e, _) in self.routed_paths if (e.from_id == nid or e.to_id == nid)]
                if not adj_edges:
                    continue

                for cand in neighbors8(current_gn):
                    if cand in self.settled_nodes.values():
                        continue
                    if not self._is_far_enough_from_settled(cand, ignore_node_id=nid):
                        continue

                    old_pos = self.settled_nodes[nid]
                    old_routed = list(self.routed_paths)

                    remaining = [(e, p) for (e, p) in old_routed if e not in adj_edges]

                    self.settled_nodes[nid] = cand
                    self.blocked_nodes, self.blocked_edges, self.shareable_nodes, self.shareable_edges = rebuild_obstacles_from_paths(remaining)

                    ok = True
                    new_local: List[Tuple[Edge, List[GridNode]]] = []
                    for e in adj_edges:
                        # FIX: smaller cap during local search => fast reject instead of hanging
                        path = self._route_single_edge(e, max_expansions=20000)
                        if path is None or len(path) < 2:
                            ok = False
                            break
                        self._apply_path_as_obstacle(path)
                        new_local.append((e, path))

                    if ok:
                        trial_paths = remaining + new_local
                        trial_score = self.compute_objective(trial_paths)["total"]
                        if trial_score < best_score:
                            best_score = trial_score
                            best_move = (nid, old_pos, cand)
                            best_paths = trial_paths
                            best_move_desc = f"{nid}: ({old_pos.gx},{old_pos.gy}) -> ({cand.gx},{cand.gy})"

                    # rollback
                    self.settled_nodes[nid] = old_pos
                    self.routed_paths = old_routed
                    self.blocked_nodes, self.blocked_edges, self.shareable_nodes, self.shareable_edges = rebuild_obstacles_from_paths(self.routed_paths)

            if best_move is None or best_paths is None:
                if verbose:
                    print(f"  Iter {it}: no improving move found -> stop")
                break

            nid, old_pos, new_pos = best_move
            self.settled_nodes[nid] = new_pos
            self.routed_paths = best_paths
            self.blocked_nodes, self.blocked_edges, self.shareable_nodes, self.shareable_edges = rebuild_obstacles_from_paths(self.routed_paths)

            metrics = self.compute_objective()
            new_total = metrics["total"]
            delta = best_global - new_total
            pct = (delta / best_global * 100.0) if best_global > 0 else 0.0
            best_global = new_total

            history.append({
                "iter": it,
                "move": best_move_desc,
                "total": metrics["total"],
                "hops": metrics["hops"],
                "bends": metrics["bends"],
                "delta": delta,
                "pct": pct,
            })

            if verbose:
                print(
                    f"  Iter {it}: move {best_move_desc} | "
                    f"Δ={delta:.2f} ({pct:.1f}%) | "
                    f"total={metrics['total']:.2f}, hops={metrics['hops']:.0f}, bends={metrics['bends']:.0f}"
                )

        after = self.compute_objective()
        if verbose:
            total_delta = before["total"] - after["total"]
            total_pct = (total_delta / before["total"] * 100.0) if before["total"] > 0 else 0.0
            print(
                f"\n  ✓ Local search done: total {before['total']:.2f} -> {after['total']:.2f} "
                f"(Δ={total_delta:.2f}, {total_pct:.1f}%)"
            )

        return {"history": history, "before": before, "after": after}

    # ---------------------- Driver / output ----------------------

    def generate(self):
        print("\n" + "=" * 60)
        print("GENERATING OCTILINEAR METRO MAP")
        print("=" * 60)

        self.calculate_line_degrees()
        

        self.compute_node_order()
        self.compute_edge_order()
        self.setup_grid()
        self.rotation_system = self.compute_rotation_system()
        self.route_all_edges()

        # Show initial objective
        before = self.compute_objective()
        print(f"\nBefore optimization: total={before['total']:.2f}, hops={before['hops']:.0f}, bends={before['bends']:.0f}")

        # Local optimization (with process logging)
        opt_stats = self.optimize_local_search(max_iters=3, verbose=True)

        # FIX: compute/print after optimization BEFORE reinsertion
        after_opt = self.compute_objective()
        print(
            f"\nAfter optimization (objective): total={after_opt['total']:.2f}, "
            f"hops={after_opt['hops']:.0f}, bends={after_opt['bends']:.0f}"
        )

        # Reinsertion AFTER optimization
        self._reinsert_contracted_degree2_nodes()

        # Print a compact per-iteration history
        if opt_stats.get("history"):
            print("\nOptimization history:")
            for h in opt_stats["history"]:
                print(
                    f"  iter {h['iter']}: {h['move']} | total={h['total']:.2f} | "
                    f"Δ={h['delta']:.2f} ({h['pct']:.1f}%)"
                )

        print("\n✓ Map generation complete!")
        print("=" * 60)
        return self.get_map_data()

    def get_map_data(self):
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "settled_nodes": self.settled_nodes,
            "routed_paths": self.routed_paths,
            "route_colors": self.route_colors,
            "grid_size": self.grid_size,
        }
