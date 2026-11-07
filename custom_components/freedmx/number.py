"""Regler: Tempo und Größe der Bewegungsmuster."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    runner = hass.data[DOMAIN]["runners"][entry.entry_id]
    add([_Num(entry, runner, "tempo", 1, 100, "mdi:speedometer"), _Num(entry, runner, "groesse", 10, 100, "mdi:resize")])


class _Num(NumberEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, entry: ConfigEntry, runner, kind: str, lo: float, hi: float, icon: str) -> None:
        self._runner = runner
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_unique_id = f"{entry.entry_id}_{kind}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        self._attr_native_min_value = lo
        self._attr_native_max_value = hi
        self._attr_icon = icon

    @property
    def native_value(self) -> float:
        return self._runner.tempo if self._kind == "tempo" else self._runner.size

    async def async_set_native_value(self, value: float) -> None:
        self._runner.set(**({"tempo": value} if self._kind == "tempo" else {"size": value}))

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runner.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
