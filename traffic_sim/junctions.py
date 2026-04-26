from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List

from .vehicles import Vehicle


@dataclass
class Junction:
    name: str
    incoming: List[str] = field(default_factory=list)
    outgoing: List[str] = field(default_factory=list)
    input_buffers: Dict[str, Deque[Vehicle]] = field(default_factory=dict)
    rr_index: int = 0

    def add_incoming(self, road_name: str):
        if road_name not in self.incoming:
            self.incoming.append(road_name)
            self.input_buffers[road_name] = deque()

    def add_outgoing(self, road_name: str):
        if road_name not in self.outgoing:
            self.outgoing.append(road_name)

    def add_source_buffer(self, source_id: str):
        if source_id not in self.input_buffers:
            self.input_buffers[source_id] = deque()

    def enqueue(self, buffer_name: str, vehicle: Vehicle):
        if buffer_name not in self.input_buffers:
            self.input_buffers[buffer_name] = deque()
        self.input_buffers[buffer_name].append(vehicle)

    def buffer_order(self) -> List[str]:
        return list(self.input_buffers.keys())
