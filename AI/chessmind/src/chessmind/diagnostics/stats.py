from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PerformanceSnapshot:
    nodes: int
    qnodes: int
    tt_hits: int
    tt_cutoffs: int
    cutoffs: int
    depth: int
    elapsed_ms: int

    @property
    def total_nodes(self):
        return self.nodes + self.qnodes

    @property
    def nps(self):
        if self.elapsed_ms <= 0:
            return 0
        return int(self.total_nodes * 1000 / self.elapsed_ms)
