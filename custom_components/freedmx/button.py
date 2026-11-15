"""Tasten: im Takt tippen und Takt ausrichten."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    engine = hass.data[DOMAIN]["beats"][entry.entry_id]
    add([
        _Btn(entry, engine, "beat_tap", "Beat tippen", "mdi:gesture-tap", engine.tap),
        _Btn(entry, engine, "beat_align", "Beat ausrichten", "mdi:align-horizontal-center", lambda: engine.align(True)),
    ])


class _Btn(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, engine, key: str, name: str, icon: str, fn) -> None:
        self._fn = fn
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        self._fn()
