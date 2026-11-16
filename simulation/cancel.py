"""Simulation cancellation for long-running Monte Carlo paths."""


class SimulationCancelled(Exception):
    """Raised when the user cancels a long-running simulation."""