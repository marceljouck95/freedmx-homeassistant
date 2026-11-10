"""Schalter: Verbindung zum freeDMX herstellen bzw. trennen."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .device import FreeDmxDevice


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback
) -> None:
    add([FreeDmxLink(hass, entry, entry.runtime_data)])


class FreeDmxLink(SwitchEntity, RestoreEntity):
    """An = Home Assistant sendet an das freeDMX. Aus = getrennt, Gerät ist frei."""

    _attr_has_entity_name = True
    _attr_translation_key = "verbindung"
    _attr_should_poll = False
    _attr_icon = "mdi:lan-connect"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device: FreeDmxDevice) -> None:
        self._device = device
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{entry.entry_id}_verbindung"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def is_on(self) -> bool:
        return self._device.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scenes = self.hass.data.get(DOMAIN, {}).get("scenes", []) if self.hass else []
        return {
            "freedmx_verbindung": True,
            "config_entry_id": self._entry_id,
            "erreichbar": self._device.connected,
            # Speicherplätze (Szenen) für die Karten: Name und enthaltene Lampen
            "speicher": [
                {"id": s["id"], "name": s["name"], "lampen": list(s.get("channels", {}))}
                for s in scenes
            ],
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            self._device.set_enabled(False)
        self.async_on_remove(self._device.add_listener(self._changed))
        links = self.hass.data.setdefault(DOMAIN, {}).setdefault("links", [])
        links.append(self)
        self.async_on_remove(lambda: links.remove(self) if self in links else None)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._device.set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._device.set_enabled(False)
        self.async_write_ha_state()

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
