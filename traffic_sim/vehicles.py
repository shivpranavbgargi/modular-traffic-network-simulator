from dataclasses import dataclass
from typing import Optional


@dataclass
class Vehicle:
    vehicle_id: int
    source: str
    destination: str
    birth_time: int
    current_node: Optional[str] = None
    prev_node: Optional[str] = None
    current_road: Optional[str] = None
    wait_time: int = 0
    finished_time: Optional[int] = None
