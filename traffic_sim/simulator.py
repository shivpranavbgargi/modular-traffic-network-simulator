import json
import os
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

from .junctions import Junction
from .roads import Road
from .router import shortest_path
from .sinks import Sink
from .sources import TrafficSource
from .vehicles import Vehicle
from .visualization import Visualizer


class TrafficSimulator:
    def __init__(
        self,
        sim_time: int = 60,
        output_dir: str = "output",
        congestion_alpha: float = 1.5,
    ):
        self.sim_time = sim_time
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # Higher alpha => routing reacts more strongly to road occupancy.
        # 0 reduces to plain shortest-travel-time routing.
        self.congestion_alpha = congestion_alpha

        self.junctions: Dict[str, Junction] = {}
        self.roads: Dict[str, Road] = {}
        self.road_lookup: Dict[tuple, str] = {}
        self.sources: List[TrafficSource] = []
        self.sinks: Dict[str, Sink] = {}
        self.junction_positions: Dict[str, tuple] = {}
        self.junction_labels: Dict[str, str] = {}

        self.vehicle_counter = 0
        self.generated = 0
        self.completed = 0
        self.total_wait_time = 0
        self.total_travel_time = 0
        self.queue_length_samples: List[int] = []
        self.completed_vehicles: List[Vehicle] = []
        self.active_vehicles: Dict[int, Vehicle] = {}
        self.current_time = 0

        # Adjacency cache, rebuilt every tick (weights depend on occupancy).
        self._adj_cache: Optional[Dict[str, List[Tuple[str, float]]]] = None
        self._adj_tick: int = -1

        self.time_series: Dict[str, list] = {
            "tick": [],
            "queue_total": [],
            "completed_cum": [],
            "throughput_per_tick": [],
            "active": [],
            "road_occupancy": defaultdict(list),
        }

    def add_junction(self, name: str, pos: Optional[tuple] = None):
        self.junctions[name] = Junction(name)
        if pos is not None:
            self.junction_positions[name] = pos

    def add_road(self, name: str, start: str, end: str, capacity: int, travel_time: int):
        if start not in self.junctions or end not in self.junctions:
            raise ValueError("Both road endpoints must be added as junctions first")
        road = Road(name, start, end, capacity, travel_time)
        self.roads[name] = road
        self.road_lookup[(start, end)] = name
        self.junctions[start].add_outgoing(name)
        self.junctions[end].add_incoming(name)

    def add_source(self, source: TrafficSource):
        if source.junction not in self.junctions:
            raise ValueError(f"Unknown source junction: {source.junction}")
        self.sources.append(source)
        self.junctions[source.junction].add_source_buffer(source.source_id)

    def add_sink(self, sink: Sink):
        if sink.junction not in self.junctions:
            raise ValueError(f"Unknown sink junction: {sink.junction}")
        self.sinks[sink.junction] = sink

    # ---------------- Adaptive routing ----------------

    def congestion_adjacency(self) -> Dict[str, List[Tuple[str, float]]]:
        """Weighted adjacency where each edge weight is

            travel_time * (1 + alpha * occupancy)

        Cached per tick so all routing decisions in the same tick agree.
        """
        if self._adj_cache is not None and self._adj_tick == self.current_time:
            return self._adj_cache
        graph: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for road in self.roads.values():
            occ = len(road.vehicles) / max(1, road.capacity)
            weight = road.travel_time * (1.0 + self.congestion_alpha * occ)
            graph[road.start].append((road.end, weight))
        self._adj_cache = dict(graph)
        self._adj_tick = self.current_time
        return self._adj_cache

    def _next_hop(self, current: str, destination: str, prev: Optional[str]) -> Optional[str]:
        adj = self.congestion_adjacency()

        # Try first to avoid an immediate U-turn (prev->current->prev). Falls
        # back to the unfiltered graph if the back-edge is the only option.
        if prev is not None and current in adj:
            filtered = dict(adj)
            filtered[current] = [(n, w) for n, w in adj[current] if n != prev]
            try:
                path = shortest_path(filtered, current, destination)
            except ValueError:
                path = shortest_path(adj, current, destination)
        else:
            path = shortest_path(adj, current, destination)

        return path[1] if len(path) >= 2 else None

    def next_road_for_vehicle(self, vehicle: Vehicle) -> Optional[Road]:
        if vehicle.current_node == vehicle.destination:
            return None
        try:
            nxt = self._next_hop(vehicle.current_node, vehicle.destination, vehicle.prev_node)
        except ValueError:
            return None
        if nxt is None:
            return None
        road_name = self.road_lookup.get((vehicle.current_node, nxt))
        return self.roads.get(road_name) if road_name else None

    # ---------------- Vehicle lifecycle ----------------

    def spawn_vehicle(self, source_junction: str, destination: str, source_buffer_id: str):
        # Verify a route exists at spawn time. We use plain travel-time weights
        # for this check (alpha=0) — congestion changes tick to tick, but
        # topological reachability does not.
        plain_adj: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for road in self.roads.values():
            plain_adj[road.start].append((road.end, road.travel_time))
        try:
            shortest_path(dict(plain_adj), source_junction, destination)
        except ValueError:
            print(
                f"[warn] no route from {source_junction} to {destination}; "
                f"skipping spawn at t={self.current_time}"
            )
            return

        self.vehicle_counter += 1
        v = Vehicle(
            vehicle_id=self.vehicle_counter,
            source=source_junction,
            destination=destination,
            birth_time=self.current_time,
            current_node=source_junction,
        )
        self.generated += 1
        self.active_vehicles[v.vehicle_id] = v
        self.junctions[source_junction].enqueue(source_buffer_id, v)

    def step_generate(self):
        for src in self.sources:
            num = src.vehicles_to_generate(self.current_time)
            for _ in range(num):
                self.spawn_vehicle(src.junction, src.destination, src.source_id)

    def step_roads(self):
        for road in self.roads.values():
            arrived = road.step()
            for vehicle in arrived:
                vehicle.prev_node = road.start
                vehicle.current_node = road.end
                self.junctions[road.end].enqueue(road.name, vehicle)

    def step_junctions(self):
        # Wait time accrues for every vehicle that was queued at the start of
        # this tick — counted once even if the vehicle moves out below.
        for junction in self.junctions.values():
            for q in junction.input_buffers.values():
                for vehicle in q:
                    vehicle.wait_time += 1

        for junction in self.junctions.values():
            buffer_names = junction.buffer_order()
            n = len(buffer_names)
            if n == 0:
                continue

            # Round-robin pass: try every buffer once, in rotation, so each
            # input lane gets at most one move per tick. rr_index advances by
            # one per tick to rotate fairness over time.
            for attempt in range(n):
                idx = (junction.rr_index + attempt) % n
                q = junction.input_buffers[buffer_names[idx]]
                if not q:
                    continue
                vehicle = q[0]

                if vehicle.current_node == vehicle.destination:
                    q.popleft()
                    vehicle.finished_time = self.current_time
                    self.completed += 1
                    self.total_wait_time += vehicle.wait_time
                    self.total_travel_time += vehicle.finished_time - vehicle.birth_time
                    self.completed_vehicles.append(vehicle)
                    self.active_vehicles.pop(vehicle.vehicle_id, None)
                    continue

                next_road = self.next_road_for_vehicle(vehicle)
                if next_road and next_road.has_space():
                    q.popleft()
                    next_road.enter(vehicle)

            junction.rr_index = (junction.rr_index + 1) % n

    def sample_queue_lengths(self):
        total_queue = sum(
            len(q)
            for junction in self.junctions.values()
            for q in junction.input_buffers.values()
        )
        self.queue_length_samples.append(total_queue)

    def sample_time_series(self):
        ts = self.time_series
        ts["tick"].append(self.current_time)
        ts["queue_total"].append(self.queue_length_samples[-1])
        prev_completed = ts["completed_cum"][-1] if ts["completed_cum"] else 0
        ts["completed_cum"].append(self.completed)
        ts["throughput_per_tick"].append(self.completed - prev_completed)
        active = len(self.active_vehicles) + sum(len(r.vehicles) for r in self.roads.values())
        ts["active"].append(active)
        for road in self.roads.values():
            ts["road_occupancy"][road.name].append(
                len(road.vehicles) / max(1, road.capacity)
            )

    def step(self):
        # Order matters: junctions first so a vehicle delivered last tick
        # waits at least one full tick before moving out. Then roads advance,
        # then sources fire — so a freshly-spawned vehicle also waits a tick
        # in its source buffer.
        self.step_junctions()
        self.step_roads()
        self.step_generate()
        self.sample_queue_lengths()
        self.sample_time_series()

    def run(self, make_gif: bool = True, fps: int = 4, make_pdf: bool = True):
        visualizer = Visualizer(self, self.output_dir, interp_steps=3)
        visualizer.render_frame(0)

        for t in range(1, self.sim_time + 1):
            self.current_time = t
            self.step()
            visualizer.render_frame(t)

        gif_path = visualizer.save_gif(fps=fps) if make_gif else None
        stats_path = self.save_stats()
        pdf_path = self.save_stats_pdf() if make_pdf else None
        return {"gif": gif_path, "stats": stats_path, "pdf": pdf_path}

    def summary_stats(self):
        avg_wait = self.total_wait_time / self.completed if self.completed else 0.0
        avg_travel = self.total_travel_time / self.completed if self.completed else 0.0
        avg_queue = (
            sum(self.queue_length_samples) / len(self.queue_length_samples)
            if self.queue_length_samples else 0.0
        )
        active = len(self.active_vehicles) + sum(len(r.vehicles) for r in self.roads.values())
        throughput = self.completed / max(1, self.sim_time)
        busiest_road = max(self.roads.values(), key=lambda r: r.total_entered) if self.roads else None
        return {
            "generated": self.generated,
            "completed": self.completed,
            "active": active,
            "avg_wait": avg_wait,
            "avg_travel_time": avg_travel,
            "avg_queue_length": avg_queue,
            "throughput": throughput,
            "busiest_road": busiest_road.name if busiest_road else None,
            "busiest_road_entries": busiest_road.total_entered if busiest_road else 0,
            "congestion_alpha": self.congestion_alpha,
        }

    def save_stats(self) -> str:
        stats_path = os.path.join(self.output_dir, "stats.json")
        with open(stats_path, "w", encoding="utf-8") as f:
            json.dump(self.summary_stats(), f, indent=2)
        return stats_path

    def save_stats_pdf(self) -> str:
        pdf_path = os.path.join(self.output_dir, "stats.pdf")
        ts = self.time_series
        ticks = ts["tick"]

        with PdfPages(pdf_path) as pdf:
            # ---------- Page 1: aggregate time series ----------
            fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))

            axes[0, 0].plot(ticks, ts["queue_total"], color="#1E88E5")
            axes[0, 0].set_title("Total queued vehicles per tick")
            axes[0, 0].set_xlabel("Tick"); axes[0, 0].set_ylabel("Queued")
            axes[0, 0].grid(True, alpha=0.3)

            axes[0, 1].plot(ticks, ts["completed_cum"], color="#43A047", label="completed")
            axes[0, 1].plot(ticks, ts["active"], color="#FB8C00", label="in-system")
            axes[0, 1].set_title("Cumulative completed vs. in-system vehicles")
            axes[0, 1].set_xlabel("Tick"); axes[0, 1].set_ylabel("Vehicles")
            axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)

            axes[1, 0].bar(ticks, ts["throughput_per_tick"], color="#8E24AA", width=1.0)
            axes[1, 0].set_title("Per-tick throughput (completions)")
            axes[1, 0].set_xlabel("Tick"); axes[1, 0].set_ylabel("Completed this tick")
            axes[1, 0].grid(True, alpha=0.3)

            wait_times = [v.wait_time for v in self.completed_vehicles]
            if wait_times:
                axes[1, 1].hist(wait_times, bins=15, color="#E53935", edgecolor="black")
            axes[1, 1].set_title("Distribution of vehicle wait times")
            axes[1, 1].set_xlabel("Wait time (ticks)"); axes[1, 1].set_ylabel("Vehicles")
            axes[1, 1].grid(True, alpha=0.3)

            stats = self.summary_stats()
            fig.suptitle(
                f"Traffic Simulator — Run summary  "
                f"(gen={stats['generated']}, done={stats['completed']}, "
                f"avg wait={stats['avg_wait']:.2f}, "
                f"throughput={stats['throughput']:.3f}/tick, "
                f"alpha={stats['congestion_alpha']})",
                fontsize=11,
                fontweight="bold",
            )
            fig.tight_layout(rect=[0, 0, 1, 0.96])
            pdf.savefig(fig)
            plt.close(fig)

            # ---------- Page 2: per-road occupancy ----------
            fig, ax = plt.subplots(figsize=(11, 8.5))
            for rname, occ in ts["road_occupancy"].items():
                ax.plot(ticks, occ, label=rname, alpha=0.85)
            ax.set_title("Per-road occupancy ratio over time")
            ax.set_xlabel("Tick")
            ax.set_ylabel("Occupancy (vehicles / capacity)")
            ax.set_ylim(0, 1.05)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="upper right", ncol=2, fontsize=8)
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        return pdf_path
