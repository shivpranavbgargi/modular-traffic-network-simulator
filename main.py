import sys
import json

from traffic_sim import Sink, TrafficSimulator, TrafficSource


def load_network(path: str, sim_time: int = 40) -> TrafficSimulator:
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(f"Error: network file '{path}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: could not parse '{path}': {e}")
        sys.exit(1)

    # Preserve the old default from this main.py unless the JSON explicitly sets sim_time.
    cfg = dict(cfg)
    cfg.setdefault("sim_time", sim_time)
    cfg.setdefault("output_dir", "output")

    try:
        return TrafficSimulator.from_json(cfg)
    except (KeyError, TypeError, ValueError) as e:
        print(f"Error: invalid network config '{path}': {e}")
        sys.exit(1)


def build_demo_network() -> TrafficSimulator:
    sim = TrafficSimulator(sim_time=40, output_dir="output", output_buffer_capacity=10)

    sim.add_junction("A", pos=(0.0, 1.0), output_buffer_capacity=10)
    sim.add_junction("B", pos=(1.2, 1.8), output_buffer_capacity=10)
    sim.add_junction("C", pos=(1.2, 0.2), output_buffer_capacity=10)
    sim.add_junction("D", pos=(2.5, 1.0), output_buffer_capacity=10)
    sim.add_junction("E", pos=(3.6, 1.0), output_buffer_capacity=10)

    sim.add_road("R1", "A", "B", capacity=3, travel_time=3)
    sim.add_road("R2", "A", "C", capacity=2, travel_time=2)
    sim.add_road("R3", "B", "D", capacity=2, travel_time=2)
    sim.add_road("R4", "C", "D", capacity=2, travel_time=3)
    sim.add_road("R5", "B", "C", capacity=1, travel_time=2)
    sim.add_road("R6", "C", "B", capacity=1, travel_time=2)
    sim.add_road("R7", "D", "E", capacity=3, travel_time=2)

    sim.add_source(TrafficSource("S1", junction="A", destination="E", mode="constant", interval=2))
    sim.add_source(TrafficSource("S2", junction="B", destination="E", mode="poisson", rate=0.35))
    sim.add_source(TrafficSource("S3", junction="C", destination="E", mode="poisson", rate=0.25))

    sim.add_sink(Sink("E"))

    return sim


def main():
    if len(sys.argv) > 1:
        sim = load_network(sys.argv[1])
    else:
        print("No network file provided — running built-in demo network.")
        sim = build_demo_network()

    outputs = sim.run(make_gif=True, fps=4)
    print("GIF:", outputs["gif"])
    print("Stats JSON:", outputs["stats"])
    print("Stats PDF:", outputs["pdf"])
    print("Summary:", sim.summary_stats())


if __name__ == "__main__":
    main()
