"""Auswahl: Bewegungsmuster und Modus."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .patterns import MODES, PATTERNS
from .beat import AUTO, MANUAL
from .beatcore import EFFECTS
from .runner import OFF

_PAT_LABELS = {OFF: "Aus", **{k: v[0] for k, v in PATTERNS.items()}}
_PAT_KEYS = {v: k for k, v in _PAT_LABELS.items()}
_MODE_KEYS = {v: k for k, v in MODES.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    runner = hass.data[DOMAIN]["runners"][entry.entry_id]
    engine = hass.data[DOMAIN]["beats"][entry.entry_id]
    add([_Sel(entry, runner, "muster"), _Sel(entry, runner, "modus"), BeatEffectSelect(entry, engine), BeatSourceSelect(entry, engine)])


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


class _BeatSel(SelectEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, engine, key: str, name: str, icon: str) -> None:
        self._engine = engine
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._engine.add_listener(self._changed))

    @callback
    def _changed(self) -> None:
        self.async_write_ha_state()


class BeatEffectSelect(_BeatSel):
    def __init__(self, entry: ConfigEntry, engine) -> None:
        super().__init__(entry, engine, "beat_effekt", "Beat-Effekt", "mdi:flash")
        self._attr_options = list(EFFECTS.values())

    @property
    def current_option(self) -> str:
        return EFFECTS.get(self._engine.settings["effect"], "Aus")

    async def async_select_option(self, option: str) -> None:
        key = next((k for k, v in EFFECTS.items() if v == option), "aus")
        self._engine.update(effect=key)


class BeatSourceSelect(_BeatSel):
    """Taktquelle: automatisch (spielender Music-Assistant-Player), ein bestimmter Player oder manuell."""

    def __init__(self, entry: ConfigEntry, engine) -> None:
        super().__init__(entry, engine, "beat_quelle", "Beat-Quelle", "mdi:speaker")

    def _labels(self) -> dict[str, str]:
        out = {AUTO: "Automatisch (Music Assistant)"}
        for p in self._engine.ma_players():
            st = self.hass.states.get(p) if self.hass else None
            out[p] = (st.attributes.get("friendly_name") if st else None) or p
        cur = self._engine.settings["source"]
        if cur not in out and cur != MANUAL:
            out[cur] = cur
        out[MANUAL] = "Manuell (Tippen)"
        return out

    @property
    def options(self) -> list[str]:
        return list(self._labels().values())

    @property
    def current_option(self) -> str:
        return self._labels().get(self._engine.settings["source"], "Automatisch (Music Assistant)")

    async def async_select_option(self, option: str) -> None:
        key = next((k for k, v in self._labels().items() if v == option), AUTO)
        self._engine.update(source=key)
