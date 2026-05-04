from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

from .vehicles import Vehicle


@dataclass
class Junction:
    """A junction with one bounded queue per outgoing road.

    This models cars waiting at the *output/exit* of a junction rather than
    cars waiting in an input buffer after they have already entered the node.
    """

    name: str
    output_buffer_capacity: int = 10
    incoming: List[str] = field(default_factory=list)
    outgoing: List[str] = field(default_factory=list)
    output_buffers: Dict[str, Deque[Vehicle]] = field(default_factory=dict)
    rr_index: int = 0

    def __post_init__(self):
        if self.output_buffer_capacity < 0:
            raise ValueError("output_buffer_capacity must be >= 0")

    def add_incoming(self, road_name: str):
        if road_name not in self.incoming:
            self.incoming.append(road_name)

    def add_outgoing(self, road_name: str):
        if road_name not in self.outgoing:
            self.outgoing.append(road_name)
            self.output_buffers[road_name] = deque()

    def has_output_space(self, road_name: str) -> bool:
        q = self.output_buffers.get(road_name)
        return q is not None and len(q) < self.output_buffer_capacity

    def enqueue_output(self, road_name: str, vehicle: Vehicle) -> bool:
        if road_name not in self.output_buffers:
            self.output_buffers[road_name] = deque()
        q = self.output_buffers[road_name]
        if len(q) >= self.output_buffer_capacity:
            return False
        q.append(vehicle)
        return True

    def output_order(self) -> List[str]:
        return list(self.output_buffers.keys())

    def queued_count(self) -> int:
        return sum(len(q) for q in self.output_buffers.values())
