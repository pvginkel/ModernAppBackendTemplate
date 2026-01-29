"""Prometheus metrics module.

Services should own their own metrics directly using prometheus_client:

    from prometheus_client import Counter, Gauge, Histogram

    class MyService:
        def __init__(self):
            self.my_counter = Counter('my_counter', 'Description')

        def do_something(self):
            self.my_counter.inc()

All metrics are automatically included in the /metrics endpoint output.
"""

from common.metrics.coordinator import MetricsUpdateCoordinator
from common.metrics.service import MetricsService

__all__ = [
    "MetricsService",
    "MetricsUpdateCoordinator",
]
