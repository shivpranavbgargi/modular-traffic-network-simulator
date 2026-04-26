import os
from typing import Dict, Tuple

import imageio.v2 as imageio
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx


# High-contrast vehicle colors.
# These are intentionally different from road congestion colors.
DEST_COLORS = [
    "#00FFFF",  # cyan
    "#FF00FF",  # magenta
    "#FFFF00",  # yellow
    "#00FF00",  # lime
    "#FFFFFF",  # white
    "#8A2BE2",  # blue violet
    "#FF69B4",  # hot pink
    "#00BFFF",  # deep sky blue
]


class Visualizer:
    def __init__(self, simulator, output_dir: str, interp_steps: int = 4):
        self.sim = simulator
        self.output_dir = output_dir
        self.frames_dir = os.path.join(output_dir, "frames")
        os.makedirs(self.frames_dir, exist_ok=True)
        self.interp_steps = interp_steps

        self.graph = nx.DiGraph()

        for jn in self.sim.junctions:
            self.graph.add_node(jn)

        for road in self.sim.roads.values():
            self.graph.add_edge(
                road.start,
                road.end,
                label=f"{road.name}\ncap={road.capacity}, tt={road.travel_time}",
            )

        self.pos = self._build_positions()
        self.dest_color_map = self._build_dest_colors()

        self.frame_paths = []
        self._prev_snapshot = None
        self._frame_index = 0

        self._source_junctions = {src.junction for src in self.sim.sources}
        self._sink_junctions = set(self.sim.sinks.keys())

        self._edge_labels = nx.get_edge_attributes(self.graph, "label")

    def _build_positions(self):
        if self.sim.junction_positions:
            return dict(self.sim.junction_positions)
        return nx.spring_layout(self.graph, seed=7)

    def _build_dest_colors(self):
        dests = sorted({src.destination for src in self.sim.sources})
        return {d: DEST_COLORS[i % len(DEST_COLORS)] for i, d in enumerate(dests)}

    def _take_snapshot(self):
        snap = {"roads": {}, "queues": {}}

        for road in self.sim.roads.values():
            snap["roads"][road.name] = [
                (v.vehicle_id, v.destination, rem) for v, rem in road.vehicles
            ]

        for jname, junction in self.sim.junctions.items():
            qlist = []
            for buf, q in junction.input_buffers.items():
                for v in q:
                    qlist.append((v.vehicle_id, v.destination))
            snap["queues"][jname] = qlist

        return snap

    def _road_positions(self, snap):
        result = {}

        for road_name, vlist in snap["roads"].items():
            road = self.sim.roads[road_name]
            total = max(1, len(vlist))

            x1, y1 = self.pos[road.start]
            x2, y2 = self.pos[road.end]

            dx = -(y2 - y1)
            dy = x2 - x1
            norm = (dx**2 + dy**2) ** 0.5 or 1.0

            for idx, (vid, dest, remaining) in enumerate(vlist):
                progress = (road.travel_time - remaining) / max(1, road.travel_time)
                progress = min(max(progress, 0.04), 0.96)

                px = x1 + progress * (x2 - x1)
                py = y1 + progress * (y2 - y1)

                # Slight perpendicular offset so vehicles on same road do not overlap fully.
                scale = 0.025 * ((idx % total) - (total - 1) / 2)
                px += scale * dx / norm
                py += scale * dy / norm

                result[vid] = (px, py, dest)

        return result

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
        source_nodes = sorted(self._source_junctions - self._sink_junctions)
        sink_nodes = sorted(self._sink_junctions - self._source_junctions)
        both_nodes = sorted(self._source_junctions & self._sink_junctions)
        transit_nodes = sorted(
            set(self.graph.nodes())
            - self._source_junctions
            - self._sink_junctions
        )

        # Neutral node fill. Source/sink is represented by border, not fill color.
        fill = "#DDE6ED"

        if transit_nodes:
            nx.draw_networkx_nodes(
                self.graph,
                self.pos,
                nodelist=transit_nodes,
                node_size=1100,
                node_color=fill,
                edgecolors="#7F8C8D",
                linewidths=1.7,
                ax=ax,
            )

        if source_nodes:
            nx.draw_networkx_nodes(
                self.graph,
                self.pos,
                nodelist=source_nodes,
                node_size=1100,
                node_color=fill,
                edgecolors="#1E88E5",
                linewidths=3.5,
                ax=ax,
            )

        if sink_nodes:
            nx.draw_networkx_nodes(
                self.graph,
                self.pos,
                nodelist=sink_nodes,
                node_size=1100,
                node_color=fill,
                edgecolors="#111111",
                linewidths=3.5,
                ax=ax,
            )

        if both_nodes:
            nx.draw_networkx_nodes(
                self.graph,
                self.pos,
                nodelist=both_nodes,
                node_size=1100,
                node_color=fill,
                edgecolors="#8E24AA",
                linewidths=3.8,
                ax=ax,
            )

        nx.draw_networkx_labels(
            self.graph,
            self.pos,
            font_size=10,
            font_weight="bold",
            ax=ax,
        )

        # Small SRC/DST markers near junctions.
        for n in self.graph.nodes():
            x, y = self.pos[n]

            if n in self._source_junctions and n in self._sink_junctions:
                tag = "SRC/DST"
                color = "#8E24AA"
            elif n in self._source_junctions:
                tag = "SRC"
                color = "#1E88E5"
            elif n in self._sink_junctions:
                tag = "DST"
                color = "#111111"
            else:
                continue

            ax.text(
                x,
                y - 0.115,
                tag,
                fontsize=6.5,
                ha="center",
                va="center",
                color=color,
                fontweight="bold",
                bbox=dict(
                    boxstyle="round,pad=0.12",
                    fc="white",
                    ec=color,
                    lw=0.8,
                    alpha=0.85,
                ),
                zorder=6,
            )

    def _emit_frame(self, t, prev, curr, alpha):
        fig, (ax, ax_s) = plt.subplots(
            1,
            2,
            figsize=(12, 6),
            gridspec_kw={"width_ratios": [3, 1]},
        )

        # ---------------- Road drawing ----------------
        edge_colors = []
        edge_widths = []

        for u, v in self.graph.edges():
            rname = self.sim.road_lookup.get((u, v))

            if rname:
                road = self.sim.roads[rname]
                occ = len(road.vehicles) / max(1, road.capacity)

                # Road congestion color: green -> yellow -> red
                r = min(1.0, 2 * occ)
                g = min(1.0, 2 * (1 - occ))

                edge_colors.append((r, g, 0.1, 0.8))
                edge_widths.append(1.8 + 4 * occ)
            else:
                edge_colors.append((0.6, 0.6, 0.6, 0.5))
                edge_widths.append(1.5)

        nx.draw_networkx_edges(
            self.graph,
            self.pos,
            ax=ax,
            arrows=True,
            arrowstyle="-|>",
            arrowsize=18,
            connectionstyle="arc3,rad=0.07",
            min_source_margin=23,
            min_target_margin=23,
            width=edge_widths,
            edge_color=edge_colors,
        )

        nx.draw_networkx_edge_labels(
            self.graph,
            self.pos,
            self._edge_labels,
            font_size=7,
            ax=ax,
            label_pos=0.35,
            bbox=dict(boxstyle="round,pad=0.1", fc="white", alpha=0.65),
        )

        self._draw_junctions(ax)

        # ---------------- Vehicle dots ----------------
        prev_pos = self._road_positions(prev)
        curr_pos = self._road_positions(curr)

        for vid in set(prev_pos) | set(curr_pos):
            dest = (curr_pos.get(vid) or prev_pos.get(vid))[2]
            color = self.dest_color_map.get(dest, "#FFFFFF")

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
                mec="black",
                mew=1.4,
                zorder=7,
                alpha=a,
            )

        # ---------------- Queued vehicles ----------------
        for junction in self.sim.junctions.values():
            bx, by = self.pos[junction.name]

            for b_idx, (buf, q) in enumerate(junction.input_buffers.items()):
                for i, v in enumerate(list(q)[:5]):
                    color = self.dest_color_map.get(v.destination, "#FFFFFF")

                    ax.plot(
                        bx - 0.08 + 0.05 * b_idx,
                        by - 0.16 - 0.035 * i,
                        "s",
                        ms=5.5,
                        color=color,
                        mec="black",
                        mew=0.7,
                        zorder=6,
                    )

                if len(q) > 5:
                    ax.text(
                        bx - 0.08 + 0.05 * b_idx,
                        by - 0.16 - 0.035 * 5,
                        f"+{len(q) - 5}",
                        fontsize=7,
                        ha="center",
                        va="top",
                    )

        # ---------------- Legend ----------------
        handles = [
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label="Source junction",
                markerfacecolor="#DDE6ED",
                markeredgecolor="#1E88E5",
                markeredgewidth=2.5,
                ms=9,
            ),
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label="Destination junction",
                markerfacecolor="#DDE6ED",
                markeredgecolor="#111111",
                markeredgewidth=2.5,
                ms=9,
            ),
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label="Source + Destination",
                markerfacecolor="#DDE6ED",
                markeredgecolor="#8E24AA",
                markeredgewidth=2.5,
                ms=9,
            ),
            plt.Line2D(
                [0],
                [0],
                marker="o",
                color="w",
                label="Transit junction",
                markerfacecolor="#DDE6ED",
                markeredgecolor="#7F8C8D",
                markeredgewidth=1.5,
                ms=9,
            ),
        ]

        for d, c in self.dest_color_map.items():
            handles.append(
                plt.Line2D(
                    [0],
                    [0],
                    marker="o",
                    color="w",
                    label=f"Vehicle → {d}",
                    markerfacecolor=c,
                    markeredgecolor="black",
                    markeredgewidth=1.2,
                    ms=8,
                )
            )

        ax.legend(handles=handles, loc="lower left", fontsize=8, framealpha=0.9)

        tick_disp = max(0.0, t - 1 + alpha) if t > 0 else 0.0
        ax.set_title(
            f"Traffic Simulation  t={tick_disp:.1f}",
            fontsize=11,
            fontweight="bold",
        )
        ax.axis("off")

        # Keep camera fixed to reduce GIF jitter.
        xs = [p[0] for p in self.pos.values()]
        ys = [p[1] for p in self.pos.values()]
        margin = 0.35
        ax.set_xlim(min(xs) - margin, max(xs) + margin)
        ax.set_ylim(min(ys) - margin, max(ys) + margin)

        # ---------------- Stats panel ----------------
        stats = self.sim.summary_stats()
        ax_s.axis("off")

        ax_s.text(
            0.5,
            0.98,
            "Statistics",
            transform=ax_s.transAxes,
            ha="center",
            va="top",
            fontsize=11,
            fontweight="bold",
        )

        rows = [
            ("Tick", str(t)),
            ("Generated", str(stats["generated"])),
            ("Completed", str(stats["completed"])),
            ("Active", str(stats["active"])),
            ("Throughput", f"{stats['throughput']:.3f}/tick"),
            ("Avg wait", f"{stats['avg_wait']:.2f} ticks"),
            ("Avg travel", f"{stats['avg_travel_time']:.2f} ticks"),
            ("Avg queue", f"{stats['avg_queue_length']:.2f}"),
            (
                "Busiest",
                str(stats["busiest_road"]) if stats["busiest_road"] else "N/A",
            ),
        ]

        y = 0.89
        for lbl, val in rows:
            ax_s.text(
                0.05,
                y,
                lbl + ":",
                transform=ax_s.transAxes,
                ha="left",
                va="top",
                fontsize=8.5,
                color="#555",
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
            y -= 0.085

        # Road occupancy bars
        ax_s.text(
            0.5,
            y - 0.01,
            "Road Occupancy",
            transform=ax_s.transAxes,
            ha="center",
            va="top",
            fontsize=9,
            fontweight="bold",
        )
        y -= 0.08

        roads = list(self.sim.roads.values())
        n = len(roads)
        bw = 0.8 / max(1, n)

        for i, road in enumerate(roads):
            occ = len(road.vehicles) / max(1, road.capacity)

            r = min(1.0, 2 * occ)
            g = min(1.0, 2 * (1 - occ))

            xl = 0.1 + i * bw

            ax_s.add_patch(
                mpatches.Rectangle(
                    (xl, y - 0.1),
                    bw * 0.85,
                    0.1,
                    fc=(0.88, 0.88, 0.88, 0.9),
                    ec="gray",
                    lw=0.5,
                    transform=ax_s.transAxes,
                    clip_on=False,
                )
            )

            if occ > 0:
                ax_s.add_patch(
                    mpatches.Rectangle(
                        (xl, y - 0.1),
                        bw * 0.85 * occ,
                        0.1,
                        fc=(r, g, 0.1, 0.9),
                        transform=ax_s.transAxes,
                        clip_on=False,
                    )
                )

            ax_s.text(
                xl + bw * 0.425,
                y - 0.12,
                road.name,
                transform=ax_s.transAxes,
                ha="center",
                va="top",
                fontsize=6,
                rotation=45 if n > 5 else 0,
            )

        ax_s.set_xlim(0, 1)
        ax_s.set_ylim(0, 1)

        fp = os.path.join(self.frames_dir, f"frame_{self._frame_index:05d}.png")

        plt.tight_layout(pad=0.3)
        plt.savefig(fp, dpi=80)
        plt.close(fig)

        self.frame_paths.append(fp)
        self._frame_index += 1

    def save_gif(self, filename="simulation.gif", fps=10):
        gif_path = os.path.join(self.output_dir, filename)
        images = [imageio.imread(f) for f in self.frame_paths]
        imageio.mimsave(gif_path, images, fps=fps)
        return gif_path
