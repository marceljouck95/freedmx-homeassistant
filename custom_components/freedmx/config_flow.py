"""Einrichtungs- und Einstellungsdialoge (keine YAML nötig)."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_BIND,
    CONF_LAMPS,
    CONF_POSITIONS,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    LAMP_CUSTOM,
    LAMP_ID,
    LAMP_LAYOUT,
    LAMP_NAME,
    LAMP_PRESET,
    LAMP_START,
    PRESET_CUSTOM,
    POS_NAME,
    POS_PAN,
    POS_TILT,
    PRESETS,
)
from .protocol import FRAME_CHANNELS as DMX_CHANNELS, layout_to_text, parse_layout
from .const import CONF_ROOM
from .geometry import (
    MOUNT_DEFAULTS,
    MOUNT_HEIGHT,
    MOUNT_PAN_INVERT,
    MOUNT_PAN_OFFSET,
    MOUNT_PAN_RANGE,
    MOUNT_TILT_INVERT,
    MOUNT_TILT_OFFSET,
    MOUNT_TILT_RANGE,
    MOUNT_X,
    MOUNT_Y,
    ROOM_DEFAULTS,
    ROOM_DEPTH,
    ROOM_LIMIT,
    ROOM_MARGIN,
    ROOM_WIDTH,
)
from .protocol import has_position
from .scenes import LAMP_MOUNT, make_position


def _num(minimum: int, maximum: int, unit: str | None = None) -> NumberSelector:
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": 1,
        "mode": NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


def _numf(minimum: float, maximum: float, step: float, unit: str | None = None) -> NumberSelector:
    config: dict[str, Any] = {
        "min": minimum,
        "max": maximum,
        "step": step,
        "mode": NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(NumberSelectorConfig(**config))


def _settings_schema(with_name: bool) -> vol.Schema:
    fields: dict = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=DEFAULT_NAME)] = TextSelector()
    fields.update(
        {
            vol.Required(CONF_HOST, default=DEFAULT_HOST): TextSelector(),
            vol.Required(CONF_PORT, default=DEFAULT_PORT): _num(1, 65535),
            vol.Optional(CONF_BIND, default=""): TextSelector(),
        }
    )
    return vol.Schema(fields)


async def _resolves(hass, host: str) -> bool:
    try:
        infos = await hass.loop.getaddrinfo(host, None, family=socket.AF_INET)
    except OSError:
        return False
    return bool(infos)


def _clean_settings(user_input: dict) -> dict:
    bind = str(user_input.get(CONF_BIND) or "").strip()
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_BIND: bind,
    }


def _bind_ok(bind: str) -> bool:
    if not bind:
        return True
    try:
        ipaddress.IPv4Address(bind)
    except ValueError:
        return False
    return True


class FreeDmxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ersteinrichtung."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            settings = _clean_settings(user_input)
            if not await _resolves(self.hass, settings[CONF_HOST]):
                errors["base"] = "cannot_resolve"
            elif not _bind_ok(settings[CONF_BIND]):
                errors["base"] = "invalid_bind"
            else:
                await self.async_set_unique_id(
                    f"{settings[CONF_HOST]}:{settings[CONF_PORT]}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(user_input[CONF_NAME]).strip() or DEFAULT_NAME,
                    data=settings,
                    options={CONF_LAMPS: []},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _settings_schema(True), user_input or {}
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return FreeDmxOptionsFlow()


class FreeDmxOptionsFlow(OptionsFlow):
    """Einstellungen und Lampenverwaltung im Bearbeitungsmodus."""

    def __init__(self) -> None:
        self._edit_id: str | None = None
        self._pending: dict | None = None

    # ---------- Hilfen ----------
    @property
    def _lamps(self) -> list[dict]:
        return [dict(l) for l in self.config_entry.options.get(CONF_LAMPS, [])]

    @property
    def _positions(self) -> list[dict]:
        return [dict(p) for p in self.config_entry.options.get(CONF_POSITIONS, [])]

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(
            data={**self.config_entry.options, **changes}
        )

    def _used_text(self, skip_id: str | None = None) -> str:
        parts = []
        for lamp in sorted(self._lamps, key=lambda l: int(l[LAMP_START])):
            if lamp[LAMP_ID] == skip_id:
                continue
            start = int(lamp[LAMP_START])
            end = start + len(parse_layout(lamp[LAMP_LAYOUT])) - 1
            parts.append(f"{start}-{end} {lamp[LAMP_NAME]}")
        return ", ".join(parts) if parts else "-"

    def _lamp_options(self) -> list[SelectOptionDict]:
        result = []
        for lamp in sorted(self._lamps, key=lambda l: int(l[LAMP_START])):
            start = int(lamp[LAMP_START])
            end = start + len(parse_layout(lamp[LAMP_LAYOUT])) - 1
            result.append(
                SelectOptionDict(
                    value=lamp[LAMP_ID],
                    label=f"{lamp[LAMP_NAME]} (Kanal {start}-{end})",
                )
            )
        return result

    # ---------- Menü ----------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        menu = ["settings", "add_lamp"]
        if self._lamps:
            menu += ["edit_lamp", "remove_lamp"]
        menu.append("room")
        menu.append("add_position")
        if self._positions:
            menu.append("remove_position")
        return self.async_show_menu(step_id="init", menu_options=menu)

    # ---------- Verbindung ----------
    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            settings = _clean_settings(user_input)
            if not _bind_ok(settings[CONF_BIND]):
                errors["base"] = "invalid_bind"
            elif await _resolves(self.hass, settings[CONF_HOST]):
                return self._save(**settings)
            else:
                errors["base"] = "cannot_resolve"
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(
                _settings_schema(False), user_input or current
            ),
            errors=errors,
        )

    # ---------- Lampe anlegen / bearbeiten ----------
    async def async_step_add_lamp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._edit_id = None
        return await self.async_step_lamp()

    async def async_step_edit_lamp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._edit_id = user_input["lamp"]
            return await self.async_step_lamp()
        return self.async_show_form(
            step_id="edit_lamp",
            data_schema=vol.Schema(
                {
                    vol.Required("lamp"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._lamp_options(),
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_lamp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        lamps = self._lamps

        if user_input is not None:
            preset = user_input[LAMP_PRESET]
            raw = (
                str(user_input.get(LAMP_CUSTOM) or "")
                if preset == PRESET_CUSTOM
                else PRESETS[preset]
            )
            start = int(user_input[LAMP_START])
            name = str(user_input[LAMP_NAME]).strip()
            try:
                layout = parse_layout(raw)
            except ValueError:
                errors[LAMP_CUSTOM] = "invalid_layout"
            else:
                if start + len(layout) - 1 > DMX_CHANNELS:
                    errors[LAMP_START] = "out_of_range"
            if not name:
                errors[LAMP_NAME] = "name_required"

            if not errors:
                previous = next(
                    (l for l in lamps if l[LAMP_ID] == self._edit_id), {}
                ) if self._edit_id else {}
                lamp = {
                    **previous,
                    LAMP_ID: self._edit_id or uuid.uuid4().hex,
                    LAMP_NAME: name,
                    LAMP_START: start,
                    LAMP_PRESET: preset,
                    LAMP_LAYOUT: layout_to_text(layout),
                }
                if has_position(layout):
                    # Zweiter Schritt: Montageort und Ausrichtung im Raum.
                    self._pending = lamp
                    return await self.async_step_lamp_mount()
                lamp.pop(LAMP_MOUNT, None)
                if self._edit_id:
                    lamps = [lamp if l[LAMP_ID] == self._edit_id else l for l in lamps]
                else:
                    lamps.append(lamp)
                return self._save(**{CONF_LAMPS: lamps})

        defaults: dict[str, Any] = {LAMP_START: 1, LAMP_PRESET: "rgb"}
        if self._edit_id:
            existing = next((l for l in lamps if l[LAMP_ID] == self._edit_id), None)
            if existing:
                defaults = {
                    LAMP_NAME: existing[LAMP_NAME],
                    LAMP_START: int(existing[LAMP_START]),
                    LAMP_PRESET: existing.get(LAMP_PRESET, PRESET_CUSTOM),
                }
                if defaults[LAMP_PRESET] == PRESET_CUSTOM:
                    defaults[LAMP_CUSTOM] = existing[LAMP_LAYOUT]

        schema = vol.Schema(
            {
                vol.Required(LAMP_NAME): TextSelector(),
                vol.Required(LAMP_START): _num(1, DMX_CHANNELS),
                vol.Required(LAMP_PRESET): SelectSelector(
                    SelectSelectorConfig(
                        options=[*PRESETS, PRESET_CUSTOM],
                        translation_key="preset",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(LAMP_CUSTOM): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="lamp",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or defaults
            ),
            errors=errors,
            description_placeholders={"used": self._used_text(self._edit_id)},
        )

    # ---------- Montage einer Lampe mit Pan/Tilt ----------
    async def async_step_lamp_mount(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        lamp = self._pending or {}
        if user_input is not None:
            lamp[LAMP_MOUNT] = {
                MOUNT_X: float(user_input[MOUNT_X]),
                MOUNT_Y: float(user_input[MOUNT_Y]),
                MOUNT_HEIGHT: float(user_input[MOUNT_HEIGHT]),
                MOUNT_PAN_RANGE: float(user_input[MOUNT_PAN_RANGE]),
                MOUNT_TILT_RANGE: float(user_input[MOUNT_TILT_RANGE]),
                MOUNT_PAN_INVERT: bool(user_input.get(MOUNT_PAN_INVERT, False)),
                MOUNT_TILT_INVERT: bool(user_input.get(MOUNT_TILT_INVERT, False)),
                MOUNT_PAN_OFFSET: float(user_input.get(MOUNT_PAN_OFFSET, 0)),
                MOUNT_TILT_OFFSET: float(user_input.get(MOUNT_TILT_OFFSET, 0)),
            }
            lamps = [l for l in self._lamps if l[LAMP_ID] != lamp[LAMP_ID]]
            lamps.append(lamp)
            self._pending = None
            return self._save(**{CONF_LAMPS: lamps})

        room = {**ROOM_DEFAULTS, **self.config_entry.options.get(CONF_ROOM, {})}
        current = {**MOUNT_DEFAULTS, **lamp.get(LAMP_MOUNT, {})}
        if LAMP_MOUNT not in lamp:
            current[MOUNT_X] = round(float(room[ROOM_WIDTH]) / 2, 2)
        width = float(room[ROOM_WIDTH])
        depth = float(room[ROOM_DEPTH])
        schema = vol.Schema(
            {
                vol.Required(MOUNT_X): _numf(0, max(width, 0.01), 0.01, "m"),
                vol.Required(MOUNT_Y): _numf(0, max(depth, 0.01), 0.01, "m"),
                vol.Required(MOUNT_HEIGHT): _numf(0.1, 20, 0.01, "m"),
                vol.Required(MOUNT_PAN_RANGE): _numf(90, 720, 1, "°"),
                vol.Required(MOUNT_TILT_RANGE): _numf(90, 360, 1, "°"),
                vol.Optional(MOUNT_PAN_INVERT): BooleanSelector(),
                vol.Optional(MOUNT_TILT_INVERT): BooleanSelector(),
                vol.Optional(MOUNT_PAN_OFFSET): _numf(-360, 360, 0.5, "°"),
                vol.Optional(MOUNT_TILT_OFFSET): _numf(-180, 180, 0.5, "°"),
            }
        )
        return self.async_show_form(
            step_id="lamp_mount",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            description_placeholders={
                "name": str(lamp.get(LAMP_NAME, "")),
                "width": f"{width:g}",
                "depth": f"{depth:g}",
            },
        )

    # ---------- Raum ----------
    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            room = {
                ROOM_WIDTH: float(user_input[ROOM_WIDTH]),
                ROOM_DEPTH: float(user_input[ROOM_DEPTH]),
                ROOM_MARGIN: float(user_input.get(ROOM_MARGIN, 0)),
                ROOM_LIMIT: bool(user_input.get(ROOM_LIMIT, False)),
            }
            return self._save(**{CONF_ROOM: room})
        current = {**ROOM_DEFAULTS, **self.config_entry.options.get(CONF_ROOM, {})}
        schema = vol.Schema(
            {
                vol.Required(ROOM_WIDTH): _numf(0.5, 100, 0.01, "m"),
                vol.Required(ROOM_DEPTH): _numf(0.5, 100, 0.01, "m"),
                vol.Optional(ROOM_MARGIN): _numf(0, 5, 0.05, "m"),
                vol.Optional(ROOM_LIMIT): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="room",
            data_schema=self.add_suggested_values_to_schema(schema, current),
        )

    # ---------- Positionen (Pan/Tilt) ----------
    async def async_step_add_position(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input[POS_NAME]).strip()
            if not name:
                errors[POS_NAME] = "name_required"
            else:
                position = make_position(
                    name, user_input[POS_PAN], user_input[POS_TILT]
                )
                return self._save(
                    **{CONF_POSITIONS: [*self._positions, position]}
                )
        schema = vol.Schema(
            {
                vol.Required(POS_NAME): TextSelector(),
                vol.Required(POS_PAN, default=50): _num(0, 100, "%"),
                vol.Required(POS_TILT, default=50): _num(0, 100, "%"),
            }
        )
        return self.async_show_form(
            step_id="add_position",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_remove_position(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            remove = set(user_input.get("positions", []))
            kept = [p for p in self._positions if p["id"] not in remove]
            return self._save(**{CONF_POSITIONS: kept})
        options = [
            SelectOptionDict(
                value=p["id"],
                label=f"{p[POS_NAME]} (Pan {p[POS_PAN]:.0f} %, Tilt {p[POS_TILT]:.0f} %)",
            )
            for p in self._positions
        ]
        return self.async_show_form(
            step_id="remove_position",
            data_schema=vol.Schema(
                {
                    vol.Required("positions"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    # ---------- Lampen entfernen ----------
    async def async_step_remove_lamp(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            remove = set(user_input.get("lamps", []))
            entry = self.config_entry
            ent_reg = er.async_get(self.hass)
            dev_reg = dr.async_get(self.hass)
            for lamp_id in remove:
                uid = f"{entry.entry_id}_{lamp_id}"
                if entity_id := ent_reg.async_get_entity_id("light", DOMAIN, uid):
                    ent_reg.async_remove(entity_id)
                if device := dev_reg.async_get_device(identifiers={(DOMAIN, uid)}):
                    dev_reg.async_update_device(
                        device.id, remove_config_entry_id=entry.entry_id
                    )
            lamps = [l for l in self._lamps if l[LAMP_ID] not in remove]
            return self._save(**{CONF_LAMPS: lamps})

        return self.async_show_form(
            step_id="remove_lamp",
            data_schema=vol.Schema(
                {
                    vol.Required("lamps"): SelectSelector(
                        SelectSelectorConfig(
                            options=self._lamp_options(),
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )
