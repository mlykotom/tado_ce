"""Tests for adaptive preheat entity ID resolution."""

from enum import Enum
import importlib.abc
import importlib.machinery
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# The standalone pytest executable does not always put the repository root on
# sys.path (notably when this file is run by its absolute path).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep these tests runnable without installing Home Assistant.  This follows
# the fallback-module pattern used by tests/test_climate_ac.py.
try:
    import homeassistant  # noqa: F401
except ImportError:
    class Event:
        """Minimal Home Assistant event used by the monitoring callback."""

        def __init__(self, data: dict[str, Any]) -> None:
            self.data = data

    class HomeAssistant:
        pass

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

from custom_components.tado_ce import adaptive_preheat
from custom_components.tado_ce.const import DOMAIN


@pytest.fixture
def manager() -> adaptive_preheat.AdaptivePreheatManager:
    """Return a manager with the minimum dependencies needed for lookup tests."""
    hass = MagicMock()
    data_loader = MagicMock()
    data_loader.home_id = "12345"
    return adaptive_preheat.AdaptivePreheatManager(
        hass,
        MagicMock(),
        data_loader=data_loader,
    )


def _add_zone(manager: adaptive_preheat.AdaptivePreheatManager) -> None:
    """Add the slug-based IDs that setup creates before registry resolution."""
    manager._zone_info["42"] = {
        "name": "Living Room",
        "slug": "living_room",
        "preheat_now_entity": "binary_sensor.living_room_preheat_now",
        "preheat_advisor_entity": "sensor.living_room_preheat_advisor",
        "climate_entity": "climate.living_room",
    }


def test_resolves_registered_entity_ids_including_area_prefix(
    manager: adaptive_preheat.AdaptivePreheatManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registered IDs are used even when Home Assistant changes their names."""
    _add_zone(manager)
    registered = {
        ("binary_sensor", DOMAIN, "tado_ce_12345_zone_42_preheat_now"):
        "binary_sensor.living_room_living_room_preheat_now",
        ("sensor", DOMAIN, "tado_ce_12345_zone_42_preheat_advisor"):
        "sensor.living_room_living_room_preheat_advisor",
        ("climate", DOMAIN, "tado_ce_12345_zone_42"):
        "climate.living_room_living_room",
    }
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = (
        lambda platform, domain, unique_id: registered.get((platform, domain, unique_id))
    )
    monkeypatch.setattr(adaptive_preheat.entity_registry, "async_get", lambda hass: registry)

    manager._resolve_zone_entity_ids("42")

    assert manager._zone_info["42"] == {
        "name": "Living Room",
        "slug": "living_room",
        "preheat_now_entity": "binary_sensor.living_room_living_room_preheat_now",
        "preheat_advisor_entity": "sensor.living_room_living_room_preheat_advisor",
        "climate_entity": "climate.living_room_living_room",
    }
    assert registry.async_get_entity_id.call_count == 3


def test_falls_back_to_slug_ids_when_entities_are_not_registered(
    manager: adaptive_preheat.AdaptivePreheatManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup remains usable while entity platforms have not registered yet."""
    _add_zone(manager)
    registry = MagicMock()
    registry.async_get_entity_id.return_value = None
    monkeypatch.setattr(adaptive_preheat.entity_registry, "async_get", lambda hass: registry)

    manager._resolve_zone_entity_ids("42")

    assert manager._zone_info["42"]["preheat_now_entity"] == (
        "binary_sensor.living_room_preheat_now"
    )
    assert manager._zone_info["42"]["preheat_advisor_entity"] == (
        "sensor.living_room_preheat_advisor"
    )
    assert manager._zone_info["42"]["climate_entity"] == "climate.living_room"


def test_re_resolves_fallback_after_entity_is_registered(
    manager: adaptive_preheat.AdaptivePreheatManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later lookup picks up an entity registered after adaptive preheat setup."""
    _add_zone(manager)
    registered_id: str | None = None
    registry = MagicMock()

    def lookup(platform: str, domain: str, unique_id: str) -> str | None:
        assert domain == DOMAIN
        if unique_id.endswith("_preheat_now"):
            return registered_id
        return None

    registry.async_get_entity_id.side_effect = lookup
    monkeypatch.setattr(adaptive_preheat.entity_registry, "async_get", lambda hass: registry)

    manager._resolve_zone_entity_ids("42")
    assert manager._zone_info["42"]["preheat_now_entity"] == (
        "binary_sensor.living_room_preheat_now"
    )

    registered_id = "binary_sensor.living_room_living_room_preheat_now"
    manager._resolve_zone_entity_ids("42")

    assert manager._zone_info["42"]["preheat_now_entity"] == registered_id


@pytest.mark.parametrize(
    ("external_temperature", "already_at_target"),
    [(19.9, True), (19.8, False)],
)
def test_already_at_target_uses_external_sensor_and_0_1_deadband(
    manager: adaptive_preheat.AdaptivePreheatManager,
    monkeypatch: pytest.MonkeyPatch,
    external_temperature: float,
    already_at_target: bool,
) -> None:
    """The configured room sensor is used with the 0.1°C preheat deadband."""
    coordinator = MagicMock()
    coordinator.data = {"zones": {"42": {"sensorDataPoints": {
        "insideTemperature": {"celsius": 18.0},
    }}}}
    manager._coordinator = coordinator
    manager._zone_config_manager = MagicMock()
    manager._zone_config_manager.get_zone_config.return_value = {
        "external_temp_sensor": "sensor.room_temperature",
    }
    manager._hass.states.get.return_value = SimpleNamespace(state=str(external_temperature))
    monkeypatch.setattr(
        adaptive_preheat, "merge_homekit_into_zone_data", lambda zone, _zone_id, _coord: zone,
    )
    monkeypatch.setattr(manager, "_get_zone_state", lambda _zone_id: coordinator.data["zones"]["42"])

    assert manager._is_already_at_target("42", "Living Room", 20.0) is already_at_target


@pytest.mark.asyncio
async def test_start_monitoring_triggers_preheat_for_area_prefixed_entity(
    manager: adaptive_preheat.AdaptivePreheatManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A state event for HA's area-prefixed entity ID starts preheating."""
    _add_zone(manager)
    manager._enabled_zones = ["42"]

    registered_entity_id = "binary_sensor.living_room_living_room_preheat_now"
    registry = MagicMock()
    registry.async_get_entity_id.return_value = registered_entity_id
    monkeypatch.setattr(adaptive_preheat.entity_registry, "async_get", lambda hass: registry)

    state_handler: list[Any] = []

    def track_state_change(hass: Any, entities: list[str], handler: Any) -> Any:
        assert entities == [registered_entity_id]
        state_handler.append(handler)
        return lambda: None

    monkeypatch.setattr(adaptive_preheat, "async_track_state_change_event", track_state_change)
    trigger = MagicMock()
    monkeypatch.setattr(manager, "_trigger_preheat", trigger)

    await manager._start_monitoring()

    assert len(state_handler) == 1
    manager._hass.async_create_task = lambda coroutine: coroutine.close()
    state_handler[0](SimpleNamespace(data={
        "entity_id": registered_entity_id,
        "old_state": SimpleNamespace(state="off"),
        "new_state": SimpleNamespace(state="on"),
    }))

    trigger.assert_called_once_with("42")
