import heapq
from typing import Dict, List, Tuple


def shortest_path(
    adjacency: Dict[str, List[Tuple[str, int]]],
    src: str,
    dst: str,
) -> List[str]:
    """
    Dijkstra shortest path on a weighted directed graph.

    adjacency maps each node to a list of (neighbour, travel_time) tuples.
    Returns the list of junction names from src to dst (inclusive).
    Raises ValueError if no route exists.
    """
    if src == dst:
        return [src]

    # (cumulative_cost, node, parent)
    heap = [(0, src, None)]
    visited: Dict[str, str | None] = {}

    while heap:
        cost, node, parent = heapq.heappop(heap)
        if node in visited:
            continue
        visited[node] = parent
        if node == dst:
            break
        for neighbour, weight in adjacency.get(node, []):
            if neighbour not in visited:
                heapq.heappush(heap, (cost + weight, neighbour, node))

    if dst not in visited:
        raise ValueError(f"No route from {src} to {dst}")

    path = []
    cur: str | None = dst
    while cur is not None:
        path.append(cur)
        cur = visited[cur]
    path.reverse()
    return path
