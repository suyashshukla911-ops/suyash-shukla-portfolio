from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EngineConfig:
    max_depth: int = 18
    time_limit_ms: int = 3000
    transposition_size: int = 500_000
    quiescence_depth: int = 8
    stop_check_interval: int = 256
    deterministic: bool = True

    def validated(self) -> "EngineConfig":
        return EngineConfig(
            max_depth=max(1, min(int(self.max_depth), 64)),
            time_limit_ms=max(1, min(int(self.time_limit_ms), 120_000)),
            transposition_size=max(10_000, min(int(self.transposition_size), 2_000_000)),
            quiescence_depth=max(0, min(int(self.quiescence_depth), 32)),
            stop_check_interval=max(1, min(int(self.stop_check_interval), 4096)),
            deterministic=bool(self.deterministic),
        )
