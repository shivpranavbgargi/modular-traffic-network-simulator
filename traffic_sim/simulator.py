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
        output_buffer_capacity: int = 10,
    ):
        self.sim_time = sim_time
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

        # Higher alpha => routing reacts more strongly to road/output-queue occupancy.
        # 0 reduces to plain shortest-travel-time routing, still respecting full queues.
        self.congestion_alpha = congestion_alpha
        self.output_buffer_capacity = output_buffer_capacity

        self.junctions: Dict[str, Junction] = {}
        self.roads: Dict[str, Road] = {}
        self.road_lookup: Dict[tuple, str] = {}
        self.sources: List[TrafficSource] = []
        self.sinks: Dict[str, Sink] = {}
        self.junction_positions: Dict[str, tuple] = {}
        self.junction_labels: Dict[str, str] = {}

        self.vehicle_counter = 0
        self.generated = 0
        self.throttled = 0
        self.completed = 0
        self.total_wait_time = 0
        self.total_travel_time = 0
        self.queue_length_samples: List[int] = []
        self.completed_vehicles: List[Vehicle] = []
        self.active_vehicles: Dict[int, Vehicle] = {}
        self.current_time = 0

        # Adjacency cache, rebuilt every tick because weights depend on occupancy.
        self._adj_cache: Optional[Dict[str, List[Tuple[str, float]]]] = None
        self._adj_tick: int = -1

        self.time_series: Dict[str, list] = {
            "tick": [],
            "queue_total": [],
            "completed_cum": [],
            "throughput_per_tick": [],
            "active": [],
            "throttled_cum": [],
            "road_occupancy": defaultdict(list),
        }

    def add_junction(
        self,
        name: str,
        pos: Optional[tuple] = None,
        output_buffer_capacity: Optional[int] = None,
    ):
        """Add a junction.

        output_buffer_capacity is the max number of vehicles allowed in each
        outgoing queue of this junction. If omitted, the simulator-level
        default is used.
        """
        cap = self.output_buffer_capacity if output_buffer_capacity is None else int(output_buffer_capacity)
        self.junctions[name] = Junction(
            name=name,
            output_buffer_capacity=cap,
        )
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
        self._invalidate_routes()

    def add_source(self, source: TrafficSource):
        if source.junction not in self.junctions:
            raise ValueError(f"Unknown source junction: {source.junction}")
        self.sources.append(source)

    def add_sink(self, sink: Sink):
        if sink.junction not in self.junctions:
            raise ValueError(f"Unknown sink junction: {sink.junction}")
        self.sinks[sink.junction] = sink

    @staticmethod
    def _read_buffer_capacity(obj, default=None):
        """Read buffer capacity from common JSON key styles."""
        if not isinstance(obj, dict):
            return default
        for key in (
            "output_buffer_capacity",
            "outputBufferCapacity",
            "buffer_capacity",
            "bufferCapacity",
            "queue_capacity",
            "queueCapacity",
        ):
            if key in obj and obj[key] is not None:
                return int(obj[key])
        return default

    @classmethod
    def from_json(cls, config: dict):
        """Build a simulator from a network JSON dictionary.

        Supported capacity fields:
          - top level: output_buffer_capacity / buffer_capacity / queue_capacity
          - per junction: same keys inside each junction object

        Example junction entry:
          {"name": "J_A", "pos": [0, 1], "output_buffer_capacity": 15}
        """
        sim = cls(
            sim_time=int(config.get("sim_time", config.get("simTime", 60))),
            output_dir=config.get("output_dir", config.get("outputDir", "output")),
            congestion_alpha=float(config.get("congestion_alpha", config.get("congestionAlpha", 1.5))),
            output_buffer_capacity=cls._read_buffer_capacity(config, 10),
        )

        for item in config.get("junctions", []):
            if isinstance(item, str):
                sim.add_junction(item)
                continue

            name = item.get("name", item.get("id"))
            if not name:
                raise ValueError(f"Junction entry missing name/id: {item}")
            pos = item.get("pos", item.get("position"))
            pos = tuple(pos) if pos is not None else None
            cap = cls._read_buffer_capacity(item, None)
            sim.add_junction(name, pos=pos, output_buffer_capacity=cap)

            label = item.get("label")
            if label is not None:
                sim.junction_labels[name] = str(label)

        for item in config.get("roads", []):
            sim.add_road(
                name=item.get("name", item.get("id")),
                start=item.get("start", item.get("from")),
                end=item.get("end", item.get("to")),
                capacity=int(item.get("capacity", 1)),
                travel_time=int(item.get("travel_time", item.get("travelTime", 1))),
            )

        for item in config.get("sources", []):
            sim.add_source(
                TrafficSource(
                    source_id=item.get("source_id", item.get("id", item.get("name", "SRC"))),
                    junction=item["junction"],
                    destination=item["destination"],
                    mode=item.get("mode", "constant"),
                    interval=int(item.get("interval", 3)),
                    rate=float(item.get("rate", 0.3)),
                )
            )

        for item in config.get("sinks", []):
            junction = item if isinstance(item, str) else item.get("junction", item.get("name", item.get("id")))
            sim.add_sink(Sink(junction=junction))

        # Optional display labels for any junction, matching the original main.py JSON format.
        for junction_name, label in config.get("labels", {}).items():
            sim.junction_labels[junction_name] = str(label)

        return sim

    @classmethod
    def from_json_file(cls, path: str):
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(json.load(f))

    def _invalidate_routes(self):
        self._adj_cache = None
        self._adj_tick = -1

    # ---------------- Adaptive routing with output-queue backpressure ----------------

    def congestion_adjacency(self) -> Dict[str, List[Tuple[str, float]]]:
        """Weighted adjacency of currently usable exits.

        An edge is usable only if the output queue at edge.start has room.
        If that queue is full, routing treats the edge as temporarily absent.
        This is what makes congestion propagate backwards to upstream nodes and sources.
        """
        if self._adj_cache is not None and self._adj_tick == self.current_time:
            return self._adj_cache

        graph: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for road in self.roads.values():
            start_junction = self.junctions[road.start]
            q = start_junction.output_buffers.get(road.name)
            q_len = len(q) if q is not None else 0

            # Full exit queue => no usable path through this road right now.
            if not start_junction.has_output_space(road.name):
                continue

            road_occ = len(road.vehicles) / max(1, road.capacity)
            queue_occ = q_len / max(1, start_junction.output_buffer_capacity)
            occ = max(road_occ, queue_occ)
            weight = road.travel_time * (1.0 + self.congestion_alpha * occ)
            graph[road.start].append((road.end, weight))

        self._adj_cache = dict(graph)
        self._adj_tick = self.current_time
        return self._adj_cache

    def _next_hop(self, current: str, destination: str, prev: Optional[str]) -> Optional[str]:
        adj = self.congestion_adjacency()

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

    def _place_vehicle_at_junction(self, vehicle: Vehicle) -> bool:
        """Complete vehicle or enqueue it into one outgoing queue.

        Returns False when no usable output path currently exists. In that case,
        the caller should keep the vehicle upstream instead of letting it enter
        and pile up inside this junction.
        """
        if vehicle.current_node == vehicle.destination:
            vehicle.finished_time = self.current_time
            self.completed += 1
            self.total_wait_time += vehicle.wait_time
            self.total_travel_time += vehicle.finished_time - vehicle.birth_time
            self.completed_vehicles.append(vehicle)
            self.active_vehicles.pop(vehicle.vehicle_id, None)
            return True

        next_road = self.next_road_for_vehicle(vehicle)
        if next_road is None:
            return False

        junction = self.junctions[vehicle.current_node]
        ok = junction.enqueue_output(next_road.name, vehicle)
        if ok:
            # The adjacency cache may now be stale because this queue length changed,
            # and it may even have become full.
            self._invalidate_routes()
        return ok

    # ---------------- Vehicle lifecycle ----------------

    def spawn_vehicle(self, source_junction: str, destination: str, source_buffer_id: str = ""):
        # Topological route check: useful warning for a permanently impossible source.
        plain_adj: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        for road in self.roads.values():
            plain_adj[road.start].append((road.end, road.travel_time))
        try:
            shortest_path(dict(plain_adj), source_junction, destination)
        except ValueError:
            print(
                f"[warn] no topological route from {source_junction} to {destination}; "
                f"skipping spawn at t={self.current_time}"
            )
            self.throttled += 1
            return False

        self.vehicle_counter += 1
        v = Vehicle(
            vehicle_id=self.vehicle_counter,
            source=source_junction,
            destination=destination,
            birth_time=self.current_time,
            current_node=source_junction,
        )

        if not self._place_vehicle_at_junction(v):
            # Dynamic congestion/backpressure: do not count as generated because
            # it never entered the simulated network.
            self.vehicle_counter -= 1
            self.throttled += 1
            return False

        self.generated += 1
        self.active_vehicles[v.vehicle_id] = v
        return True

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
                vehicle.current_road = None
                if not self._place_vehicle_at_junction(vehicle):
                    # Downstream junction has no currently usable exit. Keep the car
                    # occupying the end of this road; it will retry next tick.
                    vehicle.current_road = road.name
                    road.vehicles.insert(0, (vehicle, 0))

    def step_junctions(self):
        # Wait time accrues for vehicles sitting in output queues.
        for junction in self.junctions.values():
            for q in junction.output_buffers.values():
                for vehicle in q:
                    vehicle.wait_time += 1

        # Each outgoing road gets at most one vehicle per tick from its own queue.
        for junction in self.junctions.values():
            road_names = junction.output_order()
            n = len(road_names)
            if n == 0:
                continue

            for attempt in range(n):
                idx = (junction.rr_index + attempt) % n
                road_name = road_names[idx]
                q = junction.output_buffers[road_name]
                if not q:
                    continue
                road = self.roads[road_name]
                if road.has_space():
                    vehicle = q.popleft()
                    vehicle.current_node = None
                    road.enter(vehicle)
                    self._invalidate_routes()

            junction.rr_index = (junction.rr_index + 1) % n

    def sample_queue_lengths(self):
        total_queue = sum(j.queued_count() for j in self.junctions.values())
        self.queue_length_samples.append(total_queue)

    def sample_time_series(self):
        ts = self.time_series
        ts["tick"].append(self.current_time)
        ts["queue_total"].append(self.queue_length_samples[-1])
        prev_completed = ts["completed_cum"][-1] if ts["completed_cum"] else 0
        ts["completed_cum"].append(self.completed)
        ts["throughput_per_tick"].append(self.completed - prev_completed)
        ts["active"].append(len(self.active_vehicles))
        ts["throttled_cum"].append(self.throttled)
        for road in self.roads.values():
            ts["road_occupancy"][road.name].append(
                len(road.vehicles) / max(1, road.capacity)
            )

    def step(self):
        # Output queues push onto roads, roads advance/arrive, then sources try to inject.
        # A source injects only if some currently usable route to its destination exists.
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
        throughput = self.completed / max(1, self.sim_time)
        busiest_road = max(self.roads.values(), key=lambda r: r.total_entered) if self.roads else None
        return {
            "generated": self.generated,
            "throttled": self.throttled,
            "completed": self.completed,
            "active": len(self.active_vehicles),
            "avg_wait": avg_wait,
            "avg_travel_time": avg_travel,
            "avg_queue_length": avg_queue,
            "throughput": throughput,
            "busiest_road": busiest_road.name if busiest_road else None,
            "busiest_road_entries": busiest_road.total_entered if busiest_road else 0,
            "congestion_alpha": self.congestion_alpha,
            "output_buffer_capacity": self.output_buffer_capacity,
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
            fig, axes = plt.subplots(2, 2, figsize=(11, 8.5))

            axes[0, 0].plot(ticks, ts["queue_total"], color="#1E88E5")
            axes[0, 0].set_title("Total queued vehicles per tick")
            axes[0, 0].set_xlabel("Tick"); axes[0, 0].set_ylabel("Queued")
            axes[0, 0].grid(True, alpha=0.3)

            axes[0, 1].plot(ticks, ts["completed_cum"], color="#43A047", label="completed")
            axes[0, 1].plot(ticks, ts["active"], color="#FB8C00", label="in-system")
            axes[0, 1].plot(ticks, ts["throttled_cum"], color="#E53935", label="throttled")
            axes[0, 1].set_title("Completed vs. in-system vs. throttled")
            axes[0, 1].set_xlabel("Tick"); axes[0, 1].set_ylabel("Vehicles")
            axes[0, 1].legend(); axes[0, 1].grid(True, alpha=0.3)

            axes[1, 0].bar(ticks, ts["throughput_per_tick"], color="#8E24AA", width=1.0)
            axes[1, 0].set_title("Per-tick throughput (completions)")
            axes[1, 0].set_xlabel("Tick"); axes[1, 0].set_ylabel("Completed this tick")
            axes[1, 0].grid(True, alpha=0.3)

            wait_times = [v.wait_time for v in self.completed_vehicles]
            if wait_times:
                axes[1, 1].hist(wait_times, bins=15, color="#E53935", edgecolor="black")
            axes[1, 1].set_title("Distribution of vehicle output-queue wait times")
            axes[1, 1].set_xlabel("Wait time (ticks)"); axes[1, 1].set_ylabel("Vehicles")
            axes[1, 1].grid(True, alpha=0.3)

            stats = self.summary_stats()
            fig.suptitle(
                f"Traffic Simulator — Run summary  "
                f"(gen={stats['generated']}, throttled={stats['throttled']}, "
                f"done={stats['completed']}, avg wait={stats['avg_wait']:.2f}, "
                f"throughput={stats['throughput']:.3f}/tick, "
                f"queue cap={stats['output_buffer_capacity']})",
                fontsize=11,
                fontweight="bold",
            )
            fig.tight_layout(rect=[0, 0, 1, 0.96])
            pdf.savefig(fig)
            plt.close(fig)

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
