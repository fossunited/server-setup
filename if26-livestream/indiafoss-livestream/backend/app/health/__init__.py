"""Health package for IndiaFOSS 2026 livestream platform."""

from .watchdog import HealthWatchdog, StreamHealthSnapshot

__all__ = ["HealthWatchdog", "StreamHealthSnapshot"]
