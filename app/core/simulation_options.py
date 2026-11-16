"""Monte Carlo and run configuration — separate from field assumptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SimulationOptions:
    simulation_mode: str = "deterministic"
    planning_precision: str = "standard"
    num_paths: int = 1000
    seed: int | None = None
    mc_stock_allocation_pct: float = 70.0
    risk_tolerance: str = "moderate"

    def to_dict(self) -> dict:
        return {
            "simulation_mode": self.simulation_mode,
            "planning_precision": self.planning_precision,
            "num_paths": self.num_paths,
            "seed": self.seed,
            "mc_stock_allocation_pct": self.mc_stock_allocation_pct,
            "risk_tolerance": self.risk_tolerance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SimulationOptions:
        seed = data.get("seed")
        return cls(
            simulation_mode=data.get("simulation_mode", "deterministic"),
            planning_precision=data.get("planning_precision", "standard"),
            num_paths=int(data.get("num_paths", 1000)),
            seed=int(seed) if seed not in (None, "") else None,
            mc_stock_allocation_pct=float(data.get("mc_stock_allocation_pct", 70.0)),
            risk_tolerance=data.get("risk_tolerance", "moderate"),
        )