"""Auswahl: Bewegungsmuster und Modus."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .patterns import MODES, PATTERNS
from .runner import OFF

_PAT_LABELS = {OFF: "Aus", **{k: v[0] for k, v in PATTERNS.items()}}
_PAT_KEYS = {v: k for k, v in _PAT_LABELS.items()}
_MODE_KEYS = {v: k for k, v in MODES.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    runner = hass.data[DOMAIN]["runners"][entry.entry_id]
    add([_Sel(entry, runner, "muster"), _Sel(entry, runner, "modus")])


class _Sel(SelectEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, runner, kind: str) -> None:
        self._runner = runner
        self._kind = kind
        self._attr_translation_key = kind
        self._attr_unique_id = f"{entry.entry_id}_{kind}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        self._attr_icon = "mdi:motion-play" if kind == "muster" else "mdi:mirror"
        self._attr_options = list(_PAT_LABELS.values()) if kind == "muster" else list(MODES.values())

    @property
    def current_option(self) -> str:
        if self._kind == "muster":
            return _PAT_LABELS.get(self._runner.pattern, "Aus")
        return MODES.get(self._runner.mode, "Gleich")

    async def async_select_option(self, option: str) -> None:
        if self._kind == "muster":
            self._runner.set(pattern=_PAT_KEYS.get(option, OFF))
        else:
            self._runner.set(mode=_MODE_KEYS.get(option, "gleich"))

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._runner.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()
