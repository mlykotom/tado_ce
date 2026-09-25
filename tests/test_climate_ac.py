"""Tests for Tado AC Climate entity focusing on FAN mode temperature handling."""

from enum import Enum, IntFlag
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Fallback minimal mocks if homeassistant is not installed in the dev environment
try:
    import homeassistant
    from homeassistant.components.climate.const import HVACMode
except ImportError:
    class UnitOfTemperature(str, Enum):
        CELSIUS = "°C"
        FAHRENHEIT = "°F"

    class Platform(str, Enum):
        CLIMATE = "climate"
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        SWITCH = "switch"
        SELECT = "select"
        NUMBER = "number"
        BUTTON = "button"
        CALENDAR = "calendar"
        WATER_HEATER = "water_heater"
        DEVICE_TRACKER = "device_tracker"

    class ClimateEntityFeature(IntFlag):
        TARGET_TEMPERATURE = 1
        TURN_OFF = 2
        TURN_ON = 128
        FAN_MODE = 8
        SWING_MODE = 32
        SWING_HORIZONTAL_MODE = 64
        PRESET_MODE = 16

    class HVACAction(str, Enum):
        OFF = "off"
        HEAT = "heating"
        COOL = "cooling"
        DRY = "drying"
        FAN = "fan"
        IDLE = "idle"

    class HVACMode(str, Enum):
        OFF = "off"
        HEAT = "heat"
        COOL = "cool"
        DRY = "dry"
        FAN_ONLY = "fan_only"
        HEAT_COOL = "heat_cool"
        AUTO = "auto"

    class HomeAssistantError(Exception):
        def __init__(self, *args: Any, translation_domain: str | None = None, **kwargs: Any) -> None:
            super().__init__(*args)
            self.translation_domain = translation_domain

    class ClimateEntity:
        hass: Any = None
        entity_id: str = "climate.test"
        _attr_min_temp: float = 16.0
        _attr_max_temp: float = 30.0
        _attr_target_temperature: float | None = None
        _attr_hvac_mode: HVACMode | None = None
        _attr_hvac_action: HVACAction | None = None
        _attr_fan_mode: str | None = None
        _attr_swing_mode: str | None = None
        _attr_swing_horizontal_mode: str | None = None

        def async_write_ha_state(self) -> None:
            pass

    class RestoreEntity:
        async def async_get_last_state(self) -> Any:
            return None

    class CoordinatorEntity:
        def __class_getitem__(cls, item: Any) -> Any:
            return cls

        def __init__(self, coordinator: Any = None) -> None:
            self.coordinator = coordinator

        @property
        def name(self) -> str:
            return "test"

    _KNOWN: dict[str, Any] = {
        "homeassistant.const.ATTR_TEMPERATURE": "temperature",
        "homeassistant.const.UnitOfTemperature": UnitOfTemperature,
        "homeassistant.const.Platform": Platform,
        "homeassistant.const.STATE_UNAVAILABLE": "unavailable",
        "homeassistant.const.STATE_UNKNOWN": "unknown",
        "homeassistant.const.STATE_ON": "on",
        "homeassistant.const.STATE_OFF": "off",
        "homeassistant.const.PERCENTAGE": "%",
        "homeassistant.core.callback": lambda f: f,
        "homeassistant.core.CALLBACK_TYPE": Any,
        "homeassistant.exceptions.HomeAssistantError": HomeAssistantError,
        "homeassistant.exceptions.ConfigEntryNotReady": HomeAssistantError,
        "homeassistant.exceptions.ConfigEntryAuthFailed": HomeAssistantError,
        "homeassistant.components.climate.ATTR_HVAC_MODE": "hvac_mode",
        "homeassistant.components.climate.ClimateEntity": ClimateEntity,
        "homeassistant.components.climate.const.FAN_AUTO": "auto",
        "homeassistant.components.climate.const.FAN_HIGH": "high",
        "homeassistant.components.climate.const.FAN_LOW": "low",
        "homeassistant.components.climate.const.FAN_MEDIUM": "medium",
        "homeassistant.components.climate.const.ClimateEntityFeature": ClimateEntityFeature,
        "homeassistant.components.climate.const.HVACAction": HVACAction,
        "homeassistant.components.climate.const.HVACMode": HVACMode,
        "homeassistant.components.climate.HVACMode": HVACMode,
        "homeassistant.components.climate.HVACAction": HVACAction,
        "homeassistant.components.climate.ClimateEntityFeature": ClimateEntityFeature,
        "homeassistant.helpers.restore_state.RestoreEntity": RestoreEntity,
        "homeassistant.helpers.update_coordinator.CoordinatorEntity": CoordinatorEntity,
        "homeassistant.helpers.dispatcher.async_dispatcher_connect": lambda *a, **kw: (lambda: None),
    }

    class _AutoMockModule(ModuleType):
        def __getattr__(self, name: str) -> Any:
            full_name = f"{self.__name__}.{name}"
            if full_name in _KNOWN:
                return _KNOWN[full_name]
            val = MagicMock()
            val.__name__ = name
            setattr(self, name, val)
            return val

    class _HAMockFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname: str, path: Any, target: Any = None) -> Any:
            if fullname.startswith(("homeassistant", "aiohomekit", "voluptuous")):
                return importlib.machinery.ModuleSpec(fullname, _HAMockLoader(fullname), is_package=True)
            return None

    class _HAMockLoader(importlib.abc.Loader):
        def __init__(self, fullname: str) -> None:
            self.fullname = fullname

        def create_module(self, spec: Any) -> Any:
            mod = _AutoMockModule(self.fullname)
            mod.__path__ = []
            mod.__package__ = self.fullname
            for key, val in _KNOWN.items():
                if key.startswith(f"{self.fullname}."):
                    attr = key[len(self.fullname) + 1 :]
                    if "." not in attr:
                        setattr(mod, attr, val)
            return mod

        def exec_module(self, module: Any) -> None:
            pass

    if not any(isinstance(f, _HAMockFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _HAMockFinder())

from custom_components.tado_ce.climate_ac import TadoACClimate


def test_extract_temp_range_includes_fan():
    """Verify _extract_temp_range extracts temperature bounds from FAN mode when present."""
    ac_caps = {
        "FAN": {
            "temperatures": {
                "celsius": {"min": 18.0, "max": 28.0, "step": 0.5}
            }
        }
    }
    min_temp, max_temp, step = TadoACClimate._extract_temp_range(ac_caps)
    assert (min_temp, max_temp, step) == (18.0, 28.0, 0.5)


def test_build_ac_setting_includes_temperature_for_fan_when_supported():
    """Verify _build_ac_setting retains temperature when FAN mode has temperatures in capabilities."""
    entity = object.__new__(TadoACClimate)
    entity._capabilities = {
        "ac_capabilities": {
            "FAN": {
                "temperatures": {
                    "celsius": {"min": 16.0, "max": 30.0, "step": 1.0}
                },
                "fanLevel": ["AUTO", "LEVEL1", "LEVEL2"],
            }
        }
    }
    entity._attr_hvac_mode = HVACMode.FAN_ONLY
    entity._attr_target_temperature = 24.0
    entity._attr_min_temp = 16.0
    entity._attr_max_temp = 30.0
    entity._attr_fan_mode = "auto"
    entity._tado_to_ha_fan = {"AUTO": "auto"}
    entity._ha_to_tado_fan = {"auto": "AUTO"}

    setting = entity._build_ac_setting(
        temperature=None,
        mode="FAN",
        fan_level=None,
        vertical_swing=None,
        horizontal_swing=None,
    )

    assert setting["mode"] == "FAN"
    assert "temperature" in setting, "Temperature must be included in FAN mode when capabilities declare temperatures"
    assert setting["temperature"]["celsius"] == 24.0


def test_build_ac_setting_omits_temperature_for_fan_when_not_supported():
    """Verify _build_ac_setting omits temperature when FAN mode does not declare temperatures."""
    entity = object.__new__(TadoACClimate)
    entity._capabilities = {
        "ac_capabilities": {
            "FAN": {
                "fanLevel": ["AUTO", "LEVEL1", "LEVEL2"],
            }
        }
    }
    entity._attr_hvac_mode = HVACMode.FAN_ONLY
    entity._attr_target_temperature = 24.0
    entity._attr_min_temp = 16.0
    entity._attr_max_temp = 30.0
    entity._attr_fan_mode = "auto"
    entity._tado_to_ha_fan = {"AUTO": "auto"}
    entity._ha_to_tado_fan = {"auto": "AUTO"}

    setting = entity._build_ac_setting(
        temperature=None,
        mode="FAN",
        fan_level=None,
        vertical_swing=None,
        horizontal_swing=None,
    )

    assert setting["mode"] == "FAN"
    assert "temperature" not in setting, "Temperature must not be included when capabilities lack temperatures"


@pytest.mark.asyncio
async def test_async_set_hvac_mode_fan_preserves_target_temp():
    """Verify async_set_hvac_mode to FAN_ONLY preserves target temperature when mode supports temp."""
    entity = object.__new__(TadoACClimate)
    entity._zone_name = "Kids Room AC"
    entity._zone_id = "7"
    entity._entity_type = "ac"
    entity._capabilities = {
        "ac_capabilities": {
            "FAN": {
                "temperatures": {
                    "celsius": {"min": 16.0, "max": 30.0, "step": 1.0}
                }
            }
        }
    }
    entity._attr_min_temp = 16.0
    entity._attr_max_temp = 30.0
    entity._attr_target_temperature = 24.0
    entity._attr_hvac_mode = HVACMode.COOL
    entity._attr_fan_mode = "auto"
    entity._attr_swing_mode = None
    entity._attr_swing_horizontal_mode = None
    entity._attr_hvac_action = None
    entity._optimistic_sequence = None
    entity._overlay_type = None
    entity._entry_id = "test"
    entity.hass = MagicMock()
    entity.coordinator = MagicMock()
    entity.coordinator.mark_entity_fresh = AsyncMock()
    entity.coordinator.async_capture_state = AsyncMock()
    entity.coordinator.state_reconciler = None
    entity.async_write_ha_state = MagicMock()
    entity._calculate_hvac_action = MagicMock(return_value="fan")
    entity._async_set_ac_overlay = AsyncMock(return_value=True)

    await entity.async_set_hvac_mode(HVACMode.FAN_ONLY)

    assert entity._attr_target_temperature == 24.0, (
        "target temperature must NOT be cleared when FAN mode supports temperatures"
    )
