from dataclasses import dataclass


@dataclass
class Sink:
    """Marks a junction as a valid trip destination.

    Vehicles complete by reaching their own destination junction, not by
    looking up this object — so registering a sink is purely a hint to the
    visualizer (so it can tag the junction "DST"). Listing every destination
    junction as a sink keeps the legend honest.
    """
    junction: str
