"""DMX-Lampen als Licht-Entitäten."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .device import FreeDmxDevice
from .const import CONF_LAMPS, CONF_ROOM, DOMAIN, LAMP_ID, LAMP_LAYOUT, LAMP_NAME, LAMP_START
from .geometry import MOUNT_DEFAULTS, ROOM_DEFAULTS
from .scenes import LAMP_MOUNT, lamp_target, position_writes
from .fixtures import channel_info
from .protocol import (
    MODE_RGB,
    MODE_RGBW,
    color_mode_for,
    compute_values,
    layout_to_text,
    parse_layout,
    read_position,
)

FADE_STEP = 0.04  # Sekunden pro Überblendschritt (gleich dem Sendetakt)

_MODE_MAP = {
    MODE_RGB: ColorMode.RGB,
    MODE_RGBW: ColorMode.RGBW,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    sender: FreeDmxDevice = entry.runtime_data
    async_add_entities(
        FreeDmxLight(entry, sender, lamp, entry.options.get(CONF_ROOM))
        for lamp in entry.options.get(CONF_LAMPS, [])
    )


class FreeDmxLight(LightEntity, RestoreEntity):
    """Eine DMX-Lampe ab einer Startadresse."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(
        self, entry: ConfigEntry, sender: FreeDmxDevice, lamp: dict, room: dict | None
    ) -> None:
        self._sender = sender
        self._lamp = lamp
        self._room = {**ROOM_DEFAULTS, **(room or {})}
        self._start = int(lamp[LAMP_START])
        self._layout = parse_layout(lamp[LAMP_LAYOUT])
        self._mode = color_mode_for(self._layout)
        color_mode = _MODE_MAP.get(self._mode, ColorMode.BRIGHTNESS)
        end = self._start + len(self._layout) - 1

        self._attr_unique_id = f"{entry.entry_id}_{lamp[LAMP_ID]}"
        self._attr_color_mode = color_mode
        self._attr_supported_color_modes = {color_mode}
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_rgb_color = (255, 255, 255)
        self._attr_rgbw_color = (0, 0, 0, 255)
        self._attr_extra_state_attributes = {
            "dmx_kanaele": f"{self._start}-{end}",
            "dmx_layout": layout_to_text(self._layout),
            "lamp_id": lamp[LAMP_ID],
            "config_entry_id": entry.entry_id,
        }
        self._has_pos = any(t in ("P", "T", "p", "t") for t in self._layout)
        self._entry_id = entry.entry_id
        # dauerhaft eingestellte feste Kanäle: Index (0-basiert) -> Wert
        self._overrides: dict[int, int] = {}
        self._free_idx = {i for i, t in enumerate(self._layout) if isinstance(t, int) or t == "X"}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._attr_unique_id)},
            name=lamp[LAMP_NAME],
            manufacturer="DMX",
            model=f"Kanal {self._start}-{end} ({layout_to_text(self._layout)})",
            via_device=(DOMAIN, entry.entry_id),
        )
        self._fade_task: asyncio.Task | None = None
        self._last_pos: tuple[int, int] | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs = dict(self._attr_extra_state_attributes)
        values = self._sender.get_channels(self._start, len(self._layout))
        kanaele = channel_info(self._lamp.get("preset"), self._layout)
        for k in kanaele:
            k["wert"] = values[k["nr"] - 1]
            k["grund"] = self._layout[k["nr"] - 1] if isinstance(self._layout[k["nr"] - 1], int) else None
            k["eigen"] = (k["nr"] - 1) in self._overrides
        attrs["kanaele"] = kanaele
        attrs["kanal_werte"] = {str(i + 1): v for i, v in sorted(self._overrides.items())}
        if self._has_pos:
            attrs["raum"] = self._room
            attrs["montage"] = {**MOUNT_DEFAULTS, **self._lamp.get(LAMP_MOUNT, {})}
            attrs["montage_gesetzt"] = LAMP_MOUNT in self._lamp
            target = lamp_target(
                {**self._lamp, LAMP_MOUNT: attrs["montage"]}, self._room, self._sender._values
            )
            if target:
                attrs["ziel"] = target
        return attrs

    @property
    def available(self) -> bool:
        return self._sender.connected

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._sender.add_listener(self._handle_connection))
        registry = self.hass.data.setdefault(DOMAIN, {}).setdefault("lights", {})
        registry[self._lamp[LAMP_ID]] = self
        self.async_on_remove(lambda: registry.pop(self._lamp[LAMP_ID], None))
        if self._has_pos:
            self.async_on_remove(self._sender.add_value_listener(self._handle_values))
        last = await self.async_get_last_state()
        if last is not None:
            self._attr_is_on = last.state == STATE_ON
            if (bri := last.attributes.get(ATTR_BRIGHTNESS)) is not None:
                self._attr_brightness = int(bri)
            if (rgb := last.attributes.get(ATTR_RGB_COLOR)) and len(rgb) == 3:
                self._attr_rgb_color = tuple(int(v) for v in rgb)
            if (rgbw := last.attributes.get(ATTR_RGBW_COLOR)) and len(rgbw) == 4:
                self._attr_rgbw_color = tuple(int(v) for v in rgbw)
            saved = last.attributes.get("kanal_werte")
            if isinstance(saved, dict):
                for key, val in saved.items():
                    try:
                        idx = int(key) - 1
                    except (TypeError, ValueError):
                        continue
                    if idx in self._free_idx:
                        self._overrides[idx] = max(0, min(255, int(val)))
            ziel = last.attributes.get("ziel")
            if self._has_pos and isinstance(ziel, dict) and "pan" in ziel:
                from .protocol import percent_to_16bit

                for channel, value in position_writes(
                    self._lamp,
                    percent_to_16bit(ziel["pan"]),
                    percent_to_16bit(ziel["tilt"]),
                ):
                    self._sender.set_channels(channel, [value])
        self._sender.set_channels(self._start, self._target())

    @callback
    def _handle_values(self, first: int, last: int) -> None:
        end = self._start + len(self._layout) - 1
        if last < self._start or first > end or not self.hass:
            return
        current = read_position(self._layout, self._sender.get_channels(self._start, len(self._layout)))
        if current != self._last_pos:  # nur bei Positionsänderung neu melden
            self._last_pos = current
            self.async_write_ha_state()

    @callback
    def _handle_connection(self) -> None:
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        self._cancel_fade()

    def _color(self) -> tuple[int, ...] | None:
        if self._mode == MODE_RGB:
            return self._attr_rgb_color
        if self._mode == MODE_RGBW:
            return self._attr_rgbw_color
        return None

    def _target(self) -> list[int]:
        # Die aktuelle Position bleibt erhalten, auch beim Ein- und Ausschalten.
        current = self._sender.get_channels(self._start, len(self._layout))
        values = compute_values(
            self._layout,
            self._mode,
            bool(self._attr_is_on),
            self._attr_brightness or 0,
            self._color(),
            read_position(self._layout, current),
        )
        has_dimmer = "D" in self._layout
        if self._attr_is_on or has_dimmer:
            for idx, val in self._overrides.items():
                values[idx] = val
        return values

    def set_override(self, channel: int, value: int | None) -> None:
        """Festen Kanal (1-basiert) dauerhaft einstellen; None = Grundeinstellung."""
        idx = channel - 1
        if idx not in self._free_idx:
            raise ValueError("Dieser Kanal wird über Licht bzw. Position gesteuert.")
        if value is None:
            self._overrides.pop(idx, None)
        else:
            self._overrides[idx] = max(0, min(255, int(value)))
        self._apply(None)
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = int(kwargs[ATTR_BRIGHTNESS])
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = tuple(kwargs[ATTR_RGB_COLOR])
        if ATTR_RGBW_COLOR in kwargs:
            self._attr_rgbw_color = tuple(kwargs[ATTR_RGBW_COLOR])
        if not self._attr_brightness:
            self._attr_brightness = 255
        self._attr_is_on = True
        self._sender.free_lamps.discard(self._lamp[LAMP_ID])
        self._apply(kwargs.get(ATTR_TRANSITION))
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._attr_is_on = False
        self._apply(kwargs.get(ATTR_TRANSITION))
        if self._has_pos:
            # Grundstellung anfahren (ohne Raum-Begrenzung, Licht ist ja aus)
            from .protocol import percent_to_16bit

            m = {**MOUNT_DEFAULTS, **self._lamp.get(LAMP_MOUNT, {})}
            self._sender.free_lamps.add(self._lamp[LAMP_ID])
            for channel, value in position_writes(
                self._lamp, percent_to_16bit(m["home_pan"]), percent_to_16bit(m["home_tilt"])
            ):
                self._sender.set_channels(channel, [value])
        self.async_write_ha_state()

    def _cancel_fade(self) -> None:
        if self._fade_task and not self._fade_task.done():
            self._fade_task.cancel()
        self._fade_task = None

    def _apply(self, transition: float | None) -> None:
        self._cancel_fade()
        target = self._target()
        if transition and transition > 0:
            self._fade_task = self.hass.async_create_background_task(
                self._fade(target, float(transition)),
                name=f"freedmx_fade_{self.entity_id}",
            )
        else:
            self._sender.set_channels(self._start, target)

    async def _fade(self, target: list[int], duration: float) -> None:
        current = self._sender.get_channels(self._start, len(target))
        steps = max(1, int(duration / FADE_STEP))
        for step in range(1, steps + 1):
            values = [
                round(c + (t - c) * step / steps) for c, t in zip(current, target)
            ]
            self._sender.set_channels(self._start, values)
            await asyncio.sleep(duration / steps)
