"""Verbindungsstatus zum freeDMX."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .device import FreeDmxDevice


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([FreeDmxConnection(entry, entry.runtime_data)])


class FreeDmxConnection(BinarySensorEntity):
    """An, solange das freeDMX auf Handshake/Keepalive antwortet."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, sender: FreeDmxDevice) -> None:
        self._sender = sender
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return self._sender.connected

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "host": self._sender.host,
            "port": self._sender.port,
            "letzte_antwort": self._sender.last_reply,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._sender.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
