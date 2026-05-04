from dataclasses import dataclass, field
from typing import List, Tuple

from .vehicles import Vehicle


@dataclass
class Road:
    name: str
    start: str
    end: str
    capacity: int
    travel_time: int
    vehicles: List[Tuple[Vehicle, int]] = field(default_factory=list)
    total_entered: int = 0

    def has_space(self) -> bool:
        # Pipeline model: road can hold at most `capacity` packets,
        # BUT a new packet can only enter if the tail slot (remaining == travel_time)
        # is free — i.e. no packet entered on the same tick.
        if len(self.vehicles) >= self.capacity:
            return False
        if self.vehicles and self.vehicles[-1][1] == self.travel_time:
            return False
        return True

    def enter(self, vehicle: Vehicle) -> bool:
        if not self.has_space():
            return False
        vehicle.current_road = self.name
        self.vehicles.append((vehicle, self.travel_time))
        self.total_entered += 1
        return True

    def step(self):
        still_traveling = []
        arrived = []
        for vehicle, remaining in self.vehicles:
            remaining -= 1
            if remaining <= 0:
                vehicle.current_road = None
                arrived.append(vehicle)
            else:
                still_traveling.append((vehicle, remaining))
        self.vehicles = still_traveling
        return arrived
