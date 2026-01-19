# enhanced_visualizer.py
# Octilinear-only ENHANCED visualization:
#   - No grid background
#   - TRUE parallel lines by drawing LANES PER SEGMENT (not polyline-averaged offsets)
#   - Major/multi-line stations drawn as rotated rounded rectangles ("capsules")
#
# Drop this file next to run_heilbronn.py and import:
#   from enhanced_visualizer import EnhancedMapVisualizer

import math
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyBboxPatch
from matplotlib import transforms


class EnhancedMapVisualizer:
    """
    Produces an octilinear-only schematic map with:
      - crisp parallel line bundles (per-segment lanes)
      - capsule stations for multi-line nodes
      - optional labels
    """

    def __init__(self, map_data):
        self.map = map_data

        # Units are meters
        self.grid_size = float(self.map.get("grid_size", 200.0))

        # Styling
        self.edge_width = 3.2
        self.station_edgecolor = "#2c3e50"

        # Station sizing (in meters; scaled from grid)
        self.node_radius_m = max(35.0, self.grid_size * 0.18)  # affects circle size & capsule length baseline

        # Lane spacing: IMPORTANT (meters). Increase for stronger parallel look.
        self.sep = self.grid_size * 0.60  # e.g., 120m if grid_size=200

        # Stations: >= this many distinct routes at node -> capsule
        self.rect_threshold = 2  # set 3 if you want capsules only at bigger interchanges

        # Rounded capsule padding
        self.capsule_pad = self.grid_size * 0.02

        # Optional labels
        self.show_labels = False
        self.label_fontsize = 8

        # Fallback colors if route_colors missing
        self.colors = plt.cm.tab10.colors

        self.fig = None
        self.ax = None

    # --------------------------- Public API ---------------------------

    def create_figure(self, figsize=(12, 12), title="Enhanced Octilinear Map"):
        self.fig, self.ax = plt.subplots(1, 1, figsize=figsize)
        self.ax.set_title(title, fontsize=14)

    def draw(self):
        if self.ax is None:
            self.create_figure()
        self._draw_octilinear_enhanced()
        self._finalize_axes()

    def save(self, path: str, dpi: int = 220):
        if self.fig is None:
            raise RuntimeError("Call create_figure() / draw() before save().")
        self.fig.savefig(path, dpi=dpi, bbox_inches="tight")

    def show(self):
        plt.show()

    # --------------------------- Geometry helpers ---------------------------

    def _seg_key(self, a, b, ndigits=3):
        """Undirected segment key with rounding for float stability."""
        a = (round(a[0], ndigits), round(a[1], ndigits))
        b = (round(b[0], ndigits), round(b[1], ndigits))
        return (a, b) if a <= b else (b, a)

    def _unit(self, dx, dy):
        n = math.hypot(dx, dy)
        if n == 0:
            return (0.0, 0.0)
        return (dx / n, dy / n)

    def _normal_unit(self, dx, dy):
        """Perpendicular unit vector (normal)."""
        ux, uy = self._unit(dx, dy)
        return (-uy, ux)

    def _angle_from_vec(self, vx, vy):
        return math.degrees(math.atan2(vy, vx))

    # --------------------------- Styling helpers ---------------------------

    def _route_color(self, route_id, fallback_index=0):
        rc = self.map.get("route_colors", {})
        if route_id in rc:
            return rc[route_id]
        return self.colors[fallback_index % len(self.colors)]

    def _draw_station_circle(self, x, y):
        # Scatter "s" is in points^2, so scale with grid_size a bit
        s = max(30.0, (self.grid_size * 0.28)) ** 2 * 0.015
        self.ax.scatter(
            x,
            y,
            s=s,
            c="white",
            edgecolors=self.station_edgecolor,
            linewidths=1.4,
            zorder=8,
        )

    def _draw_station_capsule(self, x, y, angle_deg, thickness_m, length_m):
        """
        Draw a rounded rectangle (capsule) centered at (x,y), rotated by angle_deg.
        thickness_m and length_m are in meters.
        """
        w = float(length_m)
        h = float(thickness_m)

        patch = FancyBboxPatch(
            (-w / 2.0, -h / 2.0),
            w,
            h,
            boxstyle=f"round,pad={self.capsule_pad},rounding_size={h/2.0}",
            facecolor="white",
            edgecolor=self.station_edgecolor,
            linewidth=1.4,
            zorder=8,
        )
        tr = transforms.Affine2D().rotate_deg(angle_deg).translate(x, y) + self.ax.transData
        patch.set_transform(tr)
        self.ax.add_patch(patch)

    # --------------------------- Core rendering ---------------------------

    def _draw_octilinear_enhanced(self):
        """
        Draw TRUE parallel lines by expanding each segment into lanes.
        This avoids vertex-averaging and keeps segments perfectly parallel.
        """
        routed_paths = list(self.map.get("routed_paths", []))
        settled = self.map.get("settled_nodes", {})

        if not routed_paths or not isinstance(settled, dict) or not settled:
            return

        # Build per-segment route usage:
        # seg_key -> sorted list of route_ids
        seg_routes = defaultdict(set)

        # Also build per-node incident route set (for station sizing/threshold)
        node_routes = defaultdict(set)

        # Also estimate node direction (dominant track) from incident segments
        node_dirs = defaultdict(list)  # node_id -> list of unit direction vectors

        # Quick lookup from (x,y) to node_id for settled nodes
        pos_to_node = {(round(gn.x, 3), round(gn.y, 3)): nid for nid, gn in settled.items()}

        # Collect segments from all routed polylines
        for edge, path in routed_paths:
            # Node route membership from edge endpoints
            for r in getattr(edge, "routes", []) or []:
                node_routes[edge.from_id].add(r)
                node_routes[edge.to_id].add(r)

            poly = [(gn.x, gn.y) for gn in path]
            for (ax, ay), (bx, by) in zip(poly[:-1], poly[1:]):
                key = self._seg_key((ax, ay), (bx, by))
                for r in getattr(edge, "routes", []) or []:
                    seg_routes[key].add(r)

                # Direction estimate at nodes (if segment touches a settled node)
                akey = (round(ax, 3), round(ay, 3))
                bkey = (round(bx, 3), round(by, 3))
                na = pos_to_node.get(akey)
                nb = pos_to_node.get(bkey)

                vx, vy = self._unit(bx - ax, by - ay)
                if na is not None:
                    node_dirs[na].append((vx, vy))
                if nb is not None:
                    node_dirs[nb].append((vx, vy))

        # Freeze route ordering per segment (consistent & deterministic)
        seg_routes = {k: sorted(list(v)) for k, v in seg_routes.items()}

        # ------------------- Draw lane-expanded segments -------------------
        # Draw each segment once per route-lane (perfectly parallel).
        # We draw segments directly rather than drawing whole polylines.
        for i, (seg_key, routes_here) in enumerate(seg_routes.items()):
            (a, b) = seg_key
            ax, ay = a
            bx, by = b
            dx = bx - ax
            dy = by - ay
            if dx == 0 and dy == 0:
                continue

            nx, ny = self._normal_unit(dx, dy)  # perpendicular direction
            m = len(routes_here)
            if m == 0:
                continue

            # Lanes centered around 0: e.g. m=4 => [-1.5,-0.5,0.5,1.5] * sep
            for idx, route_id in enumerate(routes_here):
                lane = (idx - (m - 1) / 2.0) * self.sep
                ox = nx * lane
                oy = ny * lane

                self.ax.plot(
                    [ax + ox, bx + ox],
                    [ay + oy, by + oy],
                    linewidth=self.edge_width,
                    color=self._route_color(route_id, fallback_index=i),
                    solid_capstyle="round",
                    zorder=3,
                )

        # ------------------- Draw stations on top -------------------
        for node_id, gn in settled.items():
            x, y = gn.x, gn.y

            m = len(node_routes.get(node_id, set()))

            # Determine dominant direction (track direction) at node
            dirs = node_dirs.get(node_id, [])
            if dirs:
                avx = sum(v[0] for v in dirs) / len(dirs)
                avy = sum(v[1] for v in dirs) / len(dirs)
                track_angle = self._angle_from_vec(avx, avy)
            else:
                track_angle = 0.0

            if m >= self.rect_threshold:
                # Capsule should be perpendicular to track direction
                capsule_angle = track_angle + 90.0

                # Thickness scales with number of lines; length is modest
                thickness = max(self.node_radius_m * 0.70, (m * self.sep) + self.node_radius_m * 0.40)
                length = max(self.node_radius_m * 1.25, self.node_radius_m * 1.10)

                self._draw_station_capsule(x, y, capsule_angle, thickness_m=thickness, length_m=length)
            else:
                self._draw_station_circle(x, y)

            # Optional labels
            if self.show_labels and node_id in self.map.get("nodes", {}):
                name = self.map["nodes"][node_id].name
                self.ax.text(
                    x,
                    y + self.node_radius_m * 1.0,
                    name,
                    fontsize=self.label_fontsize,
                    ha="center",
                    va="bottom",
                    zorder=10,
                )

        # Legend
        self._draw_legend()

    def _draw_legend(self):
        route_colors = self.map.get("route_colors", {})
        if not route_colors:
            return

        handles, labels = [], []
        for route_id in sorted(route_colors.keys()):
            handles.append(plt.Line2D([0], [0], color=route_colors[route_id], linewidth=4))
            labels.append(str(route_id))

        # Put legend at bottom center like you had
        self.ax.legend(handles, labels, loc="lower center", ncol=4, frameon=False)

    def _finalize_axes(self):
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.grid(False)
        self.ax.set_xlabel("meters")
        self.ax.set_ylabel("meters")
