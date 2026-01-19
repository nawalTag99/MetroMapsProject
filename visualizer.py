import matplotlib.pyplot as plt
import numpy as np
from collections import defaultdict


class MapVisualizer:
    def __init__(self, map_data):
        self.map = map_data

        self.grid_size = self.map.get("grid_size", 200)
        self.node_radius = max(30.0, self.grid_size * 0.12)
        self.lane_sep_factor = self.map.get("lane_sep_factor", 0.35)  # NEW

        self.edge_width = 2.0
        self.geo_sep = 15.0   # meters between parallel lines on the geographic map


        self.grid_major_every = 4
        self.grid_minor_alpha = 0.12
        self.grid_major_alpha = 0.30

        self.colors = plt.cm.tab10.colors

        # Optional labels
        self.show_labels = False
        self.label_fontsize = 8

    # ------------------------------------------------------------
    def create_figure(self, figsize=(22, 10)):
        self.fig, (self.ax_geo, self.ax_oct) = plt.subplots(1, 2, figsize=figsize)
        self.ax_geo.set_title("Original Geographic Network", fontsize=14)
        self.ax_oct.set_title("Octilinear Schematic Map", fontsize=14)

    # ------------------------------------------------------------
    def draw(self):
        self._draw_geographic()
        self._draw_octilinear()
        self._finalize_axes()

    # ------------------------------------------------------------
    # LEFT PANEL — GEOGRAPHIC NETWORK
    def _draw_geographic(self):
        # Draw each physical edge ONCE (bundle shared corridors).
        # Edge color is an average of participating route colors.
        edge_routes = defaultdict(set)
        for edge in self.map["edges"]:
            key = tuple(sorted([edge.from_id, edge.to_id]))
            for rid in getattr(edge, "routes", []) or []:
                edge_routes[key].add(rid)

        for edge in self.map["edges"]:
            u = self.map["nodes"][edge.from_id]
            v = self.map["nodes"][edge.to_id]

            key = tuple(sorted([edge.from_id, edge.to_id]))
            routes_here = sorted(edge_routes.get(key, []))

            if not routes_here:
                color = "#7f8c8d"
            else:
                cols = [self.map["route_colors"].get(rid, self.colors[0]) for rid in routes_here]
                color = tuple(float(sum(c[i] for c in cols) / len(cols)) for i in range(3))

            self.ax_geo.plot(
                [u.x, v.x],
                [u.y, v.y],
                color=color,
                linewidth=2.4,
                alpha=0.85,
                zorder=2,
            )

        # Draw stops on top
        for node in self.map["nodes"].values():
            self.ax_geo.scatter(
                node.x,
                node.y,
                s=25,
                c="white",
                edgecolors="#2c3e50",
                linewidths=0.5,
                zorder=3,
            )

    # ------------------------------------------------------------
    # RIGHT PANEL — OCTILINEAR MAP
    def _draw_octilinear(self):
        self._draw_grid()

        routed = []
        for edge, path in self.map.get("routed_paths", []):
            poly = [(gn.x, gn.y) for gn in path]
            routed.append((edge, poly))

        # Draw each routed edge ONCE (bundle multi-route corridors)
        for edge, poly in routed:
            if len(poly) < 2:
                continue
            cols = [self.map["route_colors"].get(rid, self.colors[0]) for rid in (edge.routes or [])]
            if cols:
                color = tuple(float(sum(c[i] for c in cols) / len(cols)) for i in range(3))
            else:
                color = self.colors[0]

            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            lw = self.edge_width + (0.6 if len(edge.routes or []) > 1 else 0.0)

            self.ax_oct.plot(
                xs,
                ys,
                linewidth=lw,
                color=color,
                solid_capstyle="round",
                zorder=3,
            )

        # Draw nodes on top (+ optional labels)
        settled = self.map.get("settled_nodes", {})
        items = settled.items() if isinstance(settled, dict) else [(None, gn) for gn in settled]

        work_nodes = self.map.get("work_nodes")
        work_set = set(work_nodes) if work_nodes is not None else None

        for node_id, gn in items:
            is_work = (work_set is None) or (node_id is None) or (node_id in work_set)
            r = self.node_radius * (1.0 if is_work else 0.65)
            lw = 1.4 if is_work else 1.0

            self.ax_oct.scatter(
                gn.x,
                gn.y,
                s=r**2 * 0.02,
                c="white",
                edgecolors="#2c3e50",
                linewidths=lw,
                zorder=5,
            )

            if self.show_labels and node_id is not None and node_id in self.map["nodes"]:
                name = self.map["nodes"][node_id].name
                self.ax_oct.text(
                    gn.x,
                    gn.y + r * 0.55,
                    name,
                    fontsize=self.label_fontsize,
                    ha="center",
                    va="bottom",
                    zorder=6,
                )

        # ---- Label each route ONCE (anchor at longest polyline of that route) ----
        route_best = {}  # route_id -> (best_length, x, y)
        for edge, poly in routed:
            if len(poly) < 2:
                continue
            length = 0.0
            for (x1, y1), (x2, y2) in zip(poly[:-1], poly[1:]):
                length += np.hypot(x2 - x1, y2 - y1)
            midx, midy = poly[len(poly) // 2]
            for rid in (edge.routes or []):
                prev = route_best.get(rid)
                if prev is None or length > prev[0]:
                    route_best[rid] = (length, midx, midy)

        for rid, (_, x, y) in route_best.items():
            label = self.map.get("routes", {}).get(rid, rid)
            self.ax_oct.text(
                x,
                y,
                label,
                fontsize=5,
                fontweight="normal",
                ha="center",
                va="center",
                bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.55),
                zorder=6,
            )

    # ------------------------------------------------------------
    def _draw_grid(self):
        settled = self.map.get("settled_nodes", {})
        if isinstance(settled, dict):
            gns = list(settled.values())
        else:
            gns = list(settled)

        xs = [gn.x for gn in gns]
        ys = [gn.y for gn in gns]
        if not xs or not ys:
            return

        margin = self.grid_size * 3
        xmin, xmax = min(xs) - margin, max(xs) + margin
        ymin, ymax = min(ys) - margin, max(ys) + margin

        gx = np.arange(
            np.floor(xmin / self.grid_size) * self.grid_size,
            np.ceil(xmax / self.grid_size) * self.grid_size + self.grid_size,
            self.grid_size,
        )
        gy = np.arange(
            np.floor(ymin / self.grid_size) * self.grid_size,
            np.ceil(ymax / self.grid_size) * self.grid_size + self.grid_size,
            self.grid_size,
        )

        for i, x in enumerate(gx):
            alpha = self.grid_major_alpha if i % self.grid_major_every == 0 else self.grid_minor_alpha
            self.ax_oct.axvline(x, color="#7f8c8d", alpha=alpha, linewidth=0.8)

        for i, y in enumerate(gy):
            alpha = self.grid_major_alpha if i % self.grid_major_every == 0 else self.grid_minor_alpha
            self.ax_oct.axhline(y, color="#7f8c8d", alpha=alpha, linewidth=0.8)

    # ------------------------------------------------------------
    def _finalize_axes(self):
        for ax in (self.ax_geo, self.ax_oct):
            ax.set_aspect("equal", adjustable="box")
            ax.grid(False)
            ax.set_xlabel("meters")
            ax.set_ylabel("meters")

        self._draw_legend()

    # ------------------------------------------------------------
    def _draw_legend(self):
        handles, labels = [], []
        for route_id in sorted(self.map["route_colors"].keys()):
            color = self.map["route_colors"][route_id]
            handles.append(plt.Line2D([0], [0], color=color, linewidth=4))
            labels.append(self.map.get("routes", {}).get(route_id, route_id))

        if not labels:
            return

        self.fig.legend(
            handles,
            labels,
            loc="lower center",
            ncol=min(6, len(labels)),
            frameon=True,
        )

    # ------------------------------------------------------------
    def save(self, path: str, dpi: int = 300):
        self.fig.savefig(path, dpi=dpi, bbox_inches="tight")

    # ------------------------------------------------------------
    def show(self):
        plt.tight_layout(rect=[0, 0.08, 1, 1])
        plt.show()
