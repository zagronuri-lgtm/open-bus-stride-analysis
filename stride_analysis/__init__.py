"""stride_analysis — universal performance analysis for Israeli public transit.

A dynamic toolkit on top of the Open Bus Stride API. Nothing is hard-coded to a
specific operator: operators, routes and rides are all discovered at runtime, so
the same code analyses buses, Israel Railways, or a single line in a single town.

Typical use::

    from stride_analysis.data.stride_client import StrideClient
    from stride_analysis.analysis.execution import calc_execution

    client = StrideClient()
    operators = client.list_operators()                 # discover everyone
    rides = client.fetch_rides("2026-05-24", "2026-05-30", operator_ref=5)
    summary = calc_execution(rides)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
