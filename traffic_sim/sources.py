import math
import random
from dataclasses import dataclass


@dataclass
class TrafficSource:
    source_id: str
    junction: str
    destination: str
    mode: str = "constant"  # constant or poisson
    interval: int = 3        # for constant mode
    rate: float = 0.3        # lambda for poisson mode

    def vehicles_to_generate(self, t: int) -> int:
        if self.mode == "constant":
            return 1 if self.interval > 0 and t % self.interval == 0 else 0
        if self.mode == "poisson":
            return self._poisson(self.rate)
        raise ValueError(f"Unsupported source mode: {self.mode}")

    @staticmethod
    def _poisson(lam: float) -> int:
        if lam <= 0:
            return 0
        l = math.exp(-lam)
        k = 0
        p = 1.0
        while p > l:
            k += 1
            p *= random.random()
        return k - 1
