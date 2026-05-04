import os
from typing import Dict, Tuple

import imageio.v2 as imageio
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
import numpy as np

# Vehicle colors — one per destination junction.
# Chosen to be distinct from road congestion palette (green→yellow→red)
# and from node border colors (steel blue / coral / slate).
DEST_COLORS = [
    "#1E88E5",  # vivid blue
    "#8E24AA",  # purple
    "#00ACC1",  # teal/cyan
    "#3949AB",  # indigo
    "#F06292",  # pink
    "#26A69A",  # teal-green (dark enough to differ from road green)
    "#FDD835",  # yellow (bright, distinct from orange road)
    "#6D4C41",  # brown
]

# ── Junction label helpers ────────────────────────────────────────────────────
# These are now computed dynamically from the simulator in Visualizer.__init__
# so they work for any network, not just the professor grid.
_JUNCTION_LABEL = {}  # populated per-instance
_SOURCE_JUNCTIONS = set()
_SINK_JUNCTIONS = set()


class Visualizer:
    def __init__(self, simulator, output_dir: str, interp_steps: int = 6):
        self.sim = simulator
        self.output_dir = output_dir
        self.frames_dir = os.path.join(output_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.interp_steps = interp_steps

        self.graph = nx.DiGraph()
        for jn in self.sim.junctions:
            self.graph.add_node(jn)
        for road in self.sim.roads.values():
            self.graph.add_edge(road.start, road.end)

        self.pos = self._build_positions()
        self.dest_color_map = self._build_dest_colors()

        # ── Build dynamic junction labels and source/sink sets ────────────────
        # Group source IDs by junction
        src_ids_by_junction = {}
        for src in self.sim.sources:
            src_ids_by_junction.setdefault(src.junction, []).append(src.source_id)

        sink_junctions = set(self.sim.sinks.keys())  # sinks dict keyed by junction name

        global _JUNCTION_LABEL, _SOURCE_JUNCTIONS, _SINK_JUNCTIONS
        _JUNCTION_LABEL = {}
        _SOURCE_JUNCTIONS = set(src_ids_by_junction.keys())
        _SINK_JUNCTIONS = sink_junctions

        for jn in self.sim.junctions:
            if jn in src_ids_by_junction:
                _JUNCTION_LABEL[jn] = ",".join(src_ids_by_junction[jn])
            elif jn in sink_junctions:
                # Use custom label from JSON if provided, else strip J_ prefix
                custom = (
                    self.sim.junction_labels.get(jn)
                    if hasattr(self.sim, "junction_labels")
                    else None
                )
                _JUNCTION_LABEL[jn] = custom if custom else jn.replace("J_", "")

        self.frame_paths = []
        self._prev_snapshot = None
        self._frame_index = 0

        xs = [p[0] for p in self.pos.values()]
        ys = [p[1] for p in self.pos.values()]
        pad = 0.55
        self._xlim = (min(xs) - pad, max(xs) + pad)
        self._ylim = (min(ys) - pad, max(ys) + pad)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_positions(self):
        if self.sim.junction_positions:
            return dict(self.sim.junction_positions)
        return nx.spring_layout(self.graph, seed=7)

    def _build_dest_colors(self):
        dests = sorted({src.destination for src in self.sim.sources})
        return {d: DEST_COLORS[i % len(DEST_COLORS)] for i, d in enumerate(dests)}

    def _take_snapshot(self):
        snap = {"roads": {}}
        for road in self.sim.roads.values():
            snap["roads"][road.name] = [
                (v.vehicle_id, v.destination, rem) for v, rem in road.vehicles
            ]
        return snap

    # Arc radius used by draw_networkx_edges — must match the rad= value below.
    _ARC_RAD = 0.12

    def _arc_point(self, x1, y1, x2, y2, t):
        """
        Return (bx, by, ux, uy) at parameter t along the quadratic Bezier arc.
        Uses the same shortened endpoints as _draw_edges (shrunk by node_r along chord)
        and the exact Arc3 control-point formula from matplotlib:
          cx = (x1+x2)/2 + rad*(y2-y1)
          cy = (y1+y2)/2 - rad*(x2-x1)
        """
        node_r = 0.26
        rad = self._ARC_RAD
        dx, dy = x2 - x1, y2 - y1
        dist = (dx**2 + dy**2) ** 0.5 or 1.0
        ux_chord, uy_chord = dx / dist, dy / dist
        # Shortened endpoints (same as FancyArrowPatch with shrinkA=shrinkB=0)
        sx1 = x1 + node_r * ux_chord
        sy1 = y1 + node_r * uy_chord
        sx2 = x2 - node_r * ux_chord
        sy2 = y2 - node_r * uy_chord
        # Arc3 control point on shortened segment
        sdx, sdy = sx2 - sx1, sy2 - sy1
        cx = (sx1 + sx2) / 2 + rad * sdy
        cy = (sy1 + sy2) / 2 - rad * sdx
        # Quadratic Bezier position
        bx = (1 - t) ** 2 * sx1 + 2 * (1 - t) * t * cx + t**2 * sx2
        by = (1 - t) ** 2 * sy1 + 2 * (1 - t) * t * cy + t**2 * sy2
        # Tangent for perpendicular stagger
        tx = 2 * (1 - t) * (cx - sx1) + 2 * t * (sx2 - cx)
        ty = 2 * (1 - t) * (cy - sy1) + 2 * t * (sy2 - cy)
        tn = (tx**2 + ty**2) ** 0.5 or 1.0
        ux, uy = -ty / tn, tx / tn
        return bx, by, ux, uy

    def _road_positions(self, snap) -> Dict[int, Tuple[float, float, str]]:
        """Return {vid: (px, py, dest)} for every on-road vehicle."""
        result = {}
        for road_name, vlist in snap["roads"].items():
            road = self.sim.roads[road_name]
            x1, y1 = self.pos[road.start]
            x2, y2 = self.pos[road.end]

            for idx, (vid, dest, remaining) in enumerate(vlist):
                progress = (road.travel_time - remaining) / max(1, road.travel_time)
                progress = min(max(progress, 0.06), 0.94)
                bx, by, ux, uy = self._arc_point(x1, y1, x2, y2, progress)
                result[vid] = (bx, by, dest)
        return result

    # ── per-frame rendering ───────────────────────────────────────────────────

    def render_frame(self, t: int):
        curr = self._take_snapshot()
        if self._prev_snapshot is None:
            self._emit_frame(t, curr, curr, 1.0)
        else:
            for sub in range(self.interp_steps):
                alpha = (sub + 1) / self.interp_steps
                self._emit_frame(t, self._prev_snapshot, curr, alpha)
        self._prev_snapshot = curr

    def _draw_junctions(self, ax):
        node_r = 0.26  # circle radius in data coords

        for node in self.graph.nodes():
            x, y = self.pos[node]

            if node in _SOURCE_JUNCTIONS:
                fc, ec, lw = (
                    "#BBDEFB",
                    "#1565C0",
                    2.8,
                )  # light-blue fill, dark-blue border
            elif node in _SINK_JUNCTIONS:
                fc, ec, lw = (
                    "#FFE0B2",
                    "#E65100",
                    2.8,
                )  # light-amber fill, dark-orange border
            else:
                fc, ec, lw = (
                    "#F3E5F5",
                    "#6A1B9A",
                    2.0,
                )  # light-lavender fill, purple border

            circle = plt.Circle(
                (x, y),
                node_r,
                facecolor=fc,
                edgecolor=ec,
                linewidth=lw,
                zorder=5,
                transform=ax.transData,
            )
            ax.add_patch(circle)

            # Label inside circle
            lbl = _JUNCTION_LABEL.get(node, "")
            short = node.replace("J_", "")
            if lbl:
                # Source/sink: show identifier in brackets, junction name on second line
                ax.text(
                    x,
                    y + 0.06,
                    f"({lbl})",
                    ha="center",
                    va="center",
                    fontsize=5.5,
                    fontweight="bold",
                    color="#1a1a1a",
                    zorder=6,
                )
                ax.text(
                    x,
                    y - 0.07,
                    short,
                    ha="center",
                    va="center",
                    fontsize=5.5,
                    color="#333",
                    zorder=6,
                )
            else:
                # Transit: just the junction name
                ax.text(
                    x,
                    y,
                    short,
                    ha="center",
                    va="center",
                    fontsize=6.5,
                    fontweight="bold",
                    color="#1a1a1a",
                    zorder=6,
                )

    def _draw_edges(self, ax):
        """Draw road arrows using FancyArrowPatch so arc geometry exactly matches
        the Bezier used in _arc_point for vehicle placement."""
        node_r = 0.26  # same as _draw_junctions

        for u, v in self.graph.edges():
            rname = self.sim.road_lookup.get((u, v))
            if rname:
                road = self.sim.roads[rname]
                occ = len(road.vehicles) / max(1, road.capacity)
                r = min(1.0, 2 * occ)
                g = min(1.0, 2 * (1 - occ))
                color = (r, g, 0.05, 0.88)
                lw = 1.6 + 4.0 * occ
            else:
                color = (0.55, 0.55, 0.55, 0.5)
                lw = 1.2

            x1, y1 = self.pos[u]
            x2, y2 = self.pos[v]

            # Shorten endpoints by node_r along the chord so arrow starts/ends
            # at the node circle edge — keeping our arc formula unmodified.
            dx, dy = x2 - x1, y2 - y1
            dist = (dx**2 + dy**2) ** 0.5 or 1.0
            ux, uy = dx / dist, dy / dist
            sx1 = x1 + node_r * ux
            sy1 = y1 + node_r * uy
            sx2 = x2 - node_r * ux
            sy2 = y2 - node_r * uy

            arrow = mpatches.FancyArrowPatch(
                (sx1, sy1),
                (sx2, sy2),
                connectionstyle=f"arc3,rad={self._ARC_RAD}",
                arrowstyle="-|>",
                mutation_scale=14,
                linewidth=lw,
                color=color,
                shrinkA=0,
                shrinkB=0,
                zorder=2,
            )
            ax.add_patch(arrow)

    def _draw_vehicles(self, ax, prev, curr, alpha):
        prev_pos = self._road_positions(prev)
        curr_pos = self._road_positions(curr)

        for vid in set(prev_pos) | set(curr_pos):
            dest = (curr_pos.get(vid) or prev_pos.get(vid))[2]
            color = self.dest_color_map.get(dest, "#888888")

            if vid in prev_pos and vid in curr_pos:
                px = prev_pos[vid][0] + alpha * (curr_pos[vid][0] - prev_pos[vid][0])
                py = prev_pos[vid][1] + alpha * (curr_pos[vid][1] - prev_pos[vid][1])
                a = 1.0
            elif vid in curr_pos:
                px, py = curr_pos[vid][0], curr_pos[vid][1]
                a = alpha
            else:
                px, py = prev_pos[vid][0], prev_pos[vid][1]
                a = 1.0 - alpha

            ax.plot(
                px,
                py,
                "o",
                ms=10,
                color=color,
                mec="white",
                mew=2.0,
                zorder=10,
                alpha=a,
            )

    # def _draw_queues(self, ax):
    #     """Show vehicles waiting at junctions as small squares arranged in a ring."""
    #     for junction in self.sim.junctions.values():
    #         bx, by = self.pos[junction.name]
    #         queued = [v for q in junction.input_buffers.values() for v in q]
    #         total = len(queued)
    #         if total == 0:
    #             continue
    #         show = min(total, 12)
    #         for i, v in enumerate(queued[:show]):
    #             color = self.dest_color_map.get(v.destination, "#888")
    #             angle = (i / show) * 2 * np.pi - np.pi / 2
    #             r = 0.32
    #             ax.plot(bx + r * np.cos(angle),
    #                     by + r * np.sin(angle),
    #                     "s", ms=5, color=color,
    #                     mec="white", mew=0.7, zorder=9)
    #         if total > show:
    #             ax.text(bx, by + 0.42,
    #                     f"+{total - show}",
    #                     ha="center", va="bottom",
    #                     fontsize=6.5, color="#333", zorder=9)

    def _get_output_direction(self, junction_name, road_name):
        """Direction of an outgoing road relative to its start junction."""
        road = self.sim.roads.get(road_name)
        if not road:
            return None

        x1, y1 = self.pos[road.start]
        x2, y2 = self.pos[road.end]
        dx, dy = x2 - x1, y2 - y1

        if abs(dx) > abs(dy):
            return "E" if dx > 0 else "W"
        else:
            return "N" if dy > 0 else "S"

    def _get_direction(self, junction_name, road_name):
        road = self.sim.roads.get(road_name)
        if not road:
            return None
    
        xj, yj = self.pos[junction_name]
    
        # For output buffers, direction should be from junction -> road.end
        if road.start == junction_name:
            xo, yo = self.pos[road.end]
        else:
            xo, yo = self.pos[road.start]
    
        dx = xo - xj
        dy = yo - yj
    
        if abs(dx) > abs(dy):
            return "E" if dx > 0 else "W"
        else:
            return "N" if dy > 0 else "S"

    def _draw_queues(self, ax):
        """Draw output buffer queues aligned with outgoing roads, with +N support."""
        MAX_DRAW = 10
    
        for junction in self.sim.junctions.values():
            bx, by = self.pos[junction.name]
    
            # Offsets for directions
            entry_offsets = {
                "N": (0, 0.4, 0, 0.1),
                "S": (0, -0.4, 0, -0.1),
                "E": (0.4, 0, 0.1, 0),
                "W": (-0.4, 0, -0.1, 0),
            }
    
            # IMPORTANT: use output buffers now
            for road_name, q in junction.output_buffers.items():
                direction = self._get_direction(junction.name, road_name)
                if direction not in entry_offsets:
                    continue
    
                ox, oy, sx, sy = entry_offsets[direction]
                total = len(q)
    
                # Draw dots (up to MAX_DRAW)
                for i, v in enumerate(list(q)[:MAX_DRAW]):
                    color = self.dest_color_map.get(v.destination, "#888")
                    ax.plot(
                        bx + ox + (i * sx),
                        by + oy + (i * sy),
                        "o",
                        ms=5,
                        color=color,
                        mec="white",
                        mew=0.7,
                        zorder=9,
                    )
    
                # Draw +N at correct lane end
                if total > MAX_DRAW:
                    extra = total - MAX_DRAW
    
                    x = bx + ox + (MAX_DRAW * sx)
                    y = by + oy + (MAX_DRAW * sy)
    
                    # small offset outward so it doesn't overlap
                    OFFSET = 0.05
                    x += OFFSET * (1 if sx >= 0 else -1)
                    y += OFFSET * (1 if sy >= 0 else -1)
    
                    ax.text(
                        x,
                        y,
                        f"+{extra}",
                        fontsize=7,
                        color="black",
                        ha="center",
                        va="center",
                        zorder=10,
                        bbox=dict(facecolor="white", edgecolor="none", alpha=0.7),
                    )

    def _draw_stats(self, ax_s):
        ax_s.axis("off")
        stats = self.sim.summary_stats()

        ax_s.text(
            0.5,
            0.985,
            "Statistics",
            transform=ax_s.transAxes,
            ha="center",
            va="top",
            fontsize=11,
            fontweight="bold",
        )

        rows = [
            ("Tick", str(self.sim.current_time)),
            ("Generated", str(stats["generated"])),
            ("Throttled", str(stats.get("throttled", 0))),
            ("Completed", str(stats["completed"])),
            ("Active", str(stats["active"])),
            ("Throughput", f"{stats['throughput']:.2f}/tick"),
            ("Avg wait", f"{stats['avg_wait']:.1f} ticks"),
            ("Avg travel", f"{stats['avg_travel_time']:.1f} ticks"),
            ("Busiest", str(stats["busiest_road"]) if stats["busiest_road"] else "N/A"),
        ]
        y = 0.90
        for lbl, val in rows:
            ax_s.text(
                0.04,
                y,
                lbl + ":",
                transform=ax_s.transAxes,
                ha="left",
                va="top",
                fontsize=8.5,
                color="#444",
            )
            ax_s.text(
                0.97,
                y,
                val,
                transform=ax_s.transAxes,
                ha="right",
                va="top",
                fontsize=8.5,
                fontweight="bold",
            )
            y -= 0.082

        # ── Legend (replaces road occupancy bars) ─────────────────────────────
        y -= 0.02
        ax_s.text(
            0.5,
            y,
            "Legend",
            transform=ax_s.transAxes,
            ha="center",
            va="top",
            fontsize=9,
            fontweight="bold",
        )
        y -= 0.06

        # Node type swatches
        node_entries = [
            ("#BBDEFB", "#1565C0", "Source junction"),
            ("#FFE0B2", "#E65100", "Sink junction"),
            ("#F3E5F5", "#6A1B9A", "Transit junction"),
        ]
        sw = 0.10  # swatch width
        sh = 0.032  # swatch height
        gap = 0.048

        for fc, ec, label in node_entries:
            ax_s.add_patch(
                mpatches.FancyBboxPatch(
                    (0.04, y - sh),
                    sw,
                    sh,
                    boxstyle="round,pad=0.005",
                    facecolor=fc,
                    edgecolor=ec,
                    linewidth=1.5,
                    transform=ax_s.transAxes,
                    clip_on=False,
                )
            )
            ax_s.text(
                0.17,
                y - sh / 2,
                label,
                transform=ax_s.transAxes,
                ha="left",
                va="center",
                fontsize=8,
                color="#222",
            )
            y -= gap

        y -= 0.01
        ax_s.text(
            0.5,
            y,
            "Packet destinations",
            transform=ax_s.transAxes,
            ha="center",
            va="top",
            fontsize=8,
            color="#555",
        )
        y -= 0.045

        for dest, color in sorted(self.dest_color_map.items()):
            short = _JUNCTION_LABEL.get(dest, dest.replace("J_", ""))
            ax_s.plot(
                [0.04 + sw / 2],
                [y - sh / 2],
                "o",
                ms=9,
                color=color,
                mec="white",
                mew=1.2,
                transform=ax_s.transAxes,
                clip_on=False,
            )
            ax_s.text(
                0.17,
                y - sh / 2,
                f"→ {short}",
                transform=ax_s.transAxes,
                ha="left",
                va="center",
                fontsize=8,
                color="#222",
            )
            y -= gap

        ax_s.set_xlim(0, 1)
        ax_s.set_ylim(0, 1)

    def _emit_frame(self, t, prev, curr, alpha):
        fig, (ax, ax_s) = plt.subplots(
            1,
            2,
            figsize=(13, 7),
            gridspec_kw={"width_ratios": [3, 1]},
        )
        fig.patch.set_facecolor("#F5F5F5")
        ax.set_facecolor("#F5F5F5")
        ax_s.set_facecolor("#F5F5F5")

        self._draw_edges(ax)
        self._draw_junctions(ax)
        self._draw_vehicles(ax, prev, curr, alpha)
        self._draw_queues(ax)

        tick_disp = max(0.0, t - 1 + alpha) if t > 0 else 0.0
        ax.set_title(
            f"Traffic Network Simulation   t = {tick_disp:.1f}",
            fontsize=12,
            fontweight="bold",
            pad=8,
        )
        ax.axis("off")
        ax.set_xlim(self._xlim)
        ax.set_ylim(self._ylim)

        self._draw_stats(ax_s)

        fp = os.path.join(self.frames_dir, f"frame_{self._frame_index:05d}.png")
        plt.tight_layout(pad=0.5)
        plt.savefig(fp, dpi=90, facecolor=fig.get_facecolor())
        plt.close(fig)
        self.frame_paths.append(fp)
        self._frame_index += 1

    def save_gif(self, filename="simulation.gif", fps=10):
        gif_path = os.path.join(self.output_dir, filename)
        images = [imageio.imread(f) for f in self.frame_paths]
        imageio.mimsave(gif_path, images, duration=int(1000 / fps))
        return gif_path
