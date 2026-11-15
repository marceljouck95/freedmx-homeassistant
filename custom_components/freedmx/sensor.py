"""Sensor: Tempo (BPM) des Beat-Takts – Attribute enthalten alles für die Beat-Karte."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([BeatSensor(entry, hass.data[DOMAIN]["beats"][entry.entry_id])])


class BeatSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Beat Tempo"
    _attr_should_poll = False
    _attr_icon = "mdi:metronome"
    _attr_native_unit_of_measurement = "BPM"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    # Anker/Status ändern sich oft – nicht in die Datenbank schreiben
    _unrecorded_attributes = frozenset({"anker_ms", "anker_beat", "tipps", "status", "lampen_wahl", "ma_player", "effekte", "takte_wahl", "titel", "kuenstler"})

    def __init__(self, entry: ConfigEntry, engine) -> None:
        self._engine = engine
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_beat_bpm"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def native_value(self) -> float | None:
        bpm = self._engine.clock.bpm
        return round(bpm, 1) if bpm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        reg = er.async_get(self.hass)
        ent = {
            k: reg.async_get_entity_id(dom, DOMAIN, f"{self._entry_id}_{k}")
            for dom, k in (("select", "muster"), ("select", "modus"), ("number", "tempo"), ("number", "groesse"))
        }
        return {
            "freedmx_beat": True,
            "config_entry_id": self._entry_id,
            **self._engine.info(),
            "entitaeten": ent,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._engine.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
