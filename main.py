import sys
import json

from traffic_sim import Sink, TrafficSimulator, TrafficSource


def load_network(path: str, sim_time: int = 40) -> TrafficSimulator:
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        print(f"Error: network file '{path}' not found.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: could not parse '{path}': {e}")
        sys.exit(1)

    sim = TrafficSimulator(sim_time=sim_time, output_dir="output")

    for j in cfg["junctions"]:
        sim.add_junction(j["name"], pos=tuple(j["pos"]))

    for r in cfg["roads"]:
        sim.add_road(r["name"], r["from"], r["to"], r["capacity"], r["travel_time"])

    for s in cfg["sources"]:
        sim.add_source(TrafficSource(
            source_id=s["id"],
            junction=s["junction"],
            destination=s["destination"],
            mode=s.get("mode", "constant"),
            interval=s.get("interval", 3),
            rate=s.get("rate", 0.3),
        ))

    for sink_name in cfg["sinks"]:
        sim.add_sink(Sink(junction=sink_name))

    # Optional display labels for any junction (e.g. K1,K5 for sink nodes)
    for jn, lbl in cfg.get("labels", {}).items():
        sim.junction_labels[jn] = lbl

    return sim


def build_demo_network() -> TrafficSimulator:
    sim = TrafficSimulator(sim_time=40, output_dir="output")

    sim.add_junction("A", pos=(0.0, 1.0))
    sim.add_junction("B", pos=(1.2, 1.8))
    sim.add_junction("C", pos=(1.2, 0.2))
    sim.add_junction("D", pos=(2.5, 1.0))
    sim.add_junction("E", pos=(3.6, 1.0))

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
