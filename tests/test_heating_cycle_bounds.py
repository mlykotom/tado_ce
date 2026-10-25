"""Tests for heating cycle bounds and external sensor forwarding."""

from datetime import datetime, timedelta, timezone
from enum import Enum
import importlib.abc
import importlib.machinery
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import homeassistant  # noqa: F401
except ImportError:
    class Event:
        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data

    class HomeAssistant:
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

    def callback(function: Any) -> Any:
        return function

    def slugify(value: str) -> str:
        return value.lower().replace(" ", "_")

    class _UnitOfTemperature(str, Enum):
        CELSIUS = "°C"

    class _HomeAssistantError(Exception):
        pass

    _KNOWN: dict[str, Any] = {
        "homeassistant.core.Event": Event,
        "homeassistant.core.HomeAssistant": HomeAssistant,
        "homeassistant.core.callback": callback,
        "homeassistant.exceptions.HomeAssistantError": _HomeAssistantError,
        "homeassistant.helpers.entity_registry.async_get": MagicMock(),
        "homeassistant.helpers.event.async_track_state_change_event": MagicMock(
            return_value=lambda: None,
        ),
        "homeassistant.helpers.update_coordinator.DataUpdateCoordinator": DataUpdateCoordinator,
        "homeassistant.util.dt.dt_util": MagicMock(),
        "homeassistant.util.slugify": slugify,
        "homeassistant.const.UnitOfTemperature": _UnitOfTemperature,
    }

    class _HAMockModule(ModuleType):
        def __getattr__(self, name: str) -> Any:
            full_name = f"{self.__name__}.{name}"
            if full_name in _KNOWN:
                return _KNOWN[full_name]
            value = MagicMock()
            setattr(self, name, value)
            return value

    class _HAMockLoader(importlib.abc.Loader):
        def __init__(self, fullname: str) -> None:
            self.fullname = fullname

        def create_module(self, spec: Any) -> ModuleType:
            module = _HAMockModule(self.fullname)
            module.__path__ = []  # type: ignore[attr-defined]
            for key, value in _KNOWN.items():
                if key.startswith(f"{self.fullname}."):
                    name = key[len(self.fullname) + 1:]
                    if "." not in name:
                        setattr(module, name, value)
            return module

        def exec_module(self, module: ModuleType) -> None:
            pass

    class _HAMockFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
            if fullname.startswith(("homeassistant", "aiohomekit", "voluptuous")):
                return importlib.machinery.ModuleSpec(
                    fullname, _HAMockLoader(fullname), is_package=True,
                )
            return None

    if not any(isinstance(finder, _HAMockFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _HAMockFinder())

from custom_components.tado_ce.heating_analyzer import HeatingCycleAnalyzer
from custom_components.tado_ce.heating_detector import HeatingCycleDetector
from custom_components.tado_ce.heating_models import HeatingCycle, HeatingCycleConfig

UTC = timezone.utc


def _cycle(start: datetime, duration: timedelta, *, rate: float = 2.0) -> HeatingCycle:
    start_temp = 19.0
    target_temp = start_temp + rate * duration.total_seconds() / 3600
    return HeatingCycle(
        zone_id="1",
        start_time=start,
        end_time=start + duration,
        start_temp=start_temp,
        target_temp=target_temp,
        first_rise_time=start + timedelta(minutes=1),
        first_rise_temp=start_temp + 0.1,
        temperature_readings=[],
        completed=True,
        interrupted=False,
        interrupt_reason=None,
    )


def test_detector_discards_target_hit_under_minimum_duration() -> None:
    detector = HeatingCycleDetector("1", HeatingCycleConfig())
    started = datetime(2026, 1, 1, tzinfo=UTC)

    detector.check_setpoint_change(20.0, started, current_temp=19.0)
    detector.on_temperature_update(20.0, started + timedelta(minutes=4, seconds=59))

    with patch(
        "custom_components.tado_ce.heating_detector.dt_util.utcnow",
        return_value=started + timedelta(minutes=4, seconds=59),
    ):
        assert detector.check_cycle_complete() is None

    assert detector.get_active_cycle() is None


def test_detector_discards_implausible_heating_rate() -> None:
    detector = HeatingCycleDetector("1", HeatingCycleConfig())
    started = datetime(2026, 1, 1, tzinfo=UTC)

    detector.check_setpoint_change(25.0, started, current_temp=19.0)
    detector.on_temperature_update(25.0, started + timedelta(minutes=30))

    with patch(
        "custom_components.tado_ce.heating_detector.dt_util.utcnow",
        return_value=started + timedelta(minutes=30),
    ):
        assert detector.check_cycle_complete() is None

    assert detector.get_active_cycle() is None


def test_analyzer_excludes_short_and_implausible_stored_cycles() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    valid = [_cycle(start + timedelta(days=i), timedelta(minutes=10), rate=2.0) for i in range(3)]
    short = _cycle(start + timedelta(days=4), timedelta(minutes=4), rate=2.0)
    fast = _cycle(start + timedelta(days=5), timedelta(minutes=10), rate=10.1)

    metrics = HeatingCycleAnalyzer(min_cycles=3).analyze_cycles(valid + [short, fast])

    assert metrics is not None
    assert metrics["cycle_count"] == 3
    assert metrics["completed_count"] == 3
    assert metrics["heating_rate"] == 2.0


@pytest.mark.asyncio
async def test_coordinator_forwards_external_temperature_to_heating_cycle() -> None:
    from custom_components.tado_ce.coordinator import TadoDataUpdateCoordinator

    heating = SimpleNamespace(on_zone_update=AsyncMock(), clear_zone_state=Mock())
    reconciler = Mock()
    provider = SimpleNamespace(is_connected=False)
    coordinator = SimpleNamespace(
        heating_cycle_coordinator=heating,
        zone_config_manager=Mock(),
        hass=Mock(),
        state_reconciler=reconciler,
        homekit_provider=provider,
    )
    coordinator.zone_config_manager.get_zone_value.return_value = "auto"
    zone_data = {
        "zoneStates": {
            "1": {
                "setting": {"temperature": {"celsius": 20.0}},
                "sensorDataPoints": {"insideTemperature": {"celsius": 20.29}},
            },
        },
    }

    with patch(
        "custom_components.tado_ce.coordinator.read_external_sensor",
        return_value=21.0,
    ):
        await TadoDataUpdateCoordinator._notify_heating_cycle_updates(coordinator, zone_data)

    heating.on_zone_update.assert_awaited_once_with("1", 20.0, 21.0, source="cloud")
