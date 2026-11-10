"""Eurolite freeDMX AP (natives UDP-Protokoll) für Home Assistant."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_CHANNEL,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_LAMP_ID,
    ATTR_LAMP_IDS,
    ATTR_NAME,
    ATTR_POSITION_ID,
    ATTR_SCENE_ID,
    ATTR_VALUES,
    CONF_BIND,
    CONF_LAMPS,
    CONF_POSITIONS,
    CONF_ROOM,
    ATTR_X,
    ATTR_Y,
    SERVICE_AIM,
    SERVICE_CALIBRATE,
    SERVICE_MOVE,
    SERVICE_SET_LAMP_CHANNEL,
    ATTR_KANAL,
    ATTR_WERT,
    ATTR_PAN,
    ATTR_TILT,
    ATTR_FREE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
    POS_ID,
    SERVICE_DELETE_SCENE,
    SERVICE_RECALL_POSITION,
    SERVICE_RECALL_SCENE,
    SERVICE_SAVE_SCENE,
    SERVICE_SET_CHANNELS,
    STORE_KEY,
    STORE_VERSION,
)
from .device import FreeDmxDevice
from .protocol import FRAME_CHANNELS, has_position
from .geometry import (
    MOUNT_DEFAULTS,
    MOUNT_PAN_OFFSET,
    MOUNT_TILT_OFFSET,
    Mount,
    Room,
    aim_percent,
    calibrate_offsets,
    clamp_point,
)
from .protocol import percent_to_16bit
from .scenes import (
    LAMP_MOUNT,
    make_guard,
    lamp_span,
    make_scene,
    position_16bit,
    position_writes,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_CHANNELS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_CHANNEL): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=FRAME_CHANNELS)
        ),
        vol.Required(ATTR_VALUES): vol.All(
            cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=255))]
        ),
    }
)


SAVE_SCENE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(ATTR_LAMP_IDS): vol.All(cv.ensure_list, [cv.string]),
    }
)
SCENE_ID_SCHEMA = vol.Schema({vol.Required(ATTR_SCENE_ID): cv.string})
RECALL_POSITION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_POSITION_ID): cv.string,
        vol.Optional(ATTR_LAMP_ID): cv.string,
    }
)

AIM_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_LAMP_ID): cv.string,
        vol.Required(ATTR_X): vol.Coerce(float),
        vol.Required(ATTR_Y): vol.Coerce(float),
    }
)

MOVE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_LAMP_ID): cv.string,
        vol.Required(ATTR_PAN): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Required(ATTR_TILT): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional(ATTR_FREE, default=False): cv.boolean,
    }
)

LAMP_CHANNEL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Required(ATTR_LAMP_ID): cv.string,
        vol.Required(ATTR_KANAL): vol.All(vol.Coerce(int), vol.Range(min=1, max=512)),
        vol.Optional(ATTR_WERT): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0, max=255))),
    }
)


def merged_config(entry: ConfigEntry) -> dict:
    """Optionen überschreiben die Ersteinrichtung."""
    return {**entry.data, **entry.options}


def _target_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    loaded = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if entry_id:
        loaded = [e for e in loaded if e.entry_id == entry_id]
    if len(loaded) != 1:
        raise ServiceValidationError(
            "Bitte genau ein freeDMX-Gerät angeben (config_entry_id)."
        )
    return loaded[0]


CARD_URL = "/freedmx_static/freedmx-raum-card.js"


async def _register_card(hass: HomeAssistant) -> None:
    """Karte aus dem Integrationsordner ausliefern (wird mit HACS mit aktualisiert)."""
    from pathlib import Path

    from homeassistant.components.http import StaticPathConfig

    path = Path(__file__).parent / "www" / "freedmx-raum-card.js"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(path), False)]
        )
    except RuntimeError:  # schon registriert (z. B. nach Neuladen)
        pass


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Dienste registrieren: Rohwerte, Szenen und Positionen."""
    if hass.http is not None:
        await _register_card(hass)
    store: Store = Store(hass, STORE_VERSION, STORE_KEY)
    data = await store.async_load() or {}
    scenes: list[dict] = list(data.get("scenes", []))
    hass.data.setdefault(DOMAIN, {})["store"] = store
    hass.data[DOMAIN]["scenes"] = scenes

    async def save_scenes() -> None:
        await store.async_save({"scenes": hass.data[DOMAIN]["scenes"]})
        for link in hass.data[DOMAIN].get("links", []):  # Karten lesen die Liste aus dem Schalter
            link.async_write_ha_state()

    async def handle_set_channels(call: ServiceCall) -> None:
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        channel = call.data[ATTR_CHANNEL]
        values = call.data[ATTR_VALUES]
        if channel - 1 + len(values) > FRAME_CHANNELS:
            raise ServiceValidationError("Kanalbereich überschreitet 512.")
        entry.runtime_data.set_channels(channel, values)

    async def handle_save_scene(call: ServiceCall) -> None:
        """Speicherplatz anlegen: alle Kanäle (Position, Farbe, Gobo …) der gewählten Lampen.

        Ohne lamp_ids werden alle Lampen gespeichert. Gleicher Name + gleiche Lampen = überschreiben.
        """
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        lamps = merged_config(entry).get(CONF_LAMPS, [])
        only = call.data.get(ATTR_LAMP_IDS)
        lights = hass.data[DOMAIN].get("lights", {})
        snapshots, states = {}, {}
        for lamp in lamps:
            if only and lamp["id"] not in only:
                continue
            start, length = lamp_span(lamp)
            snapshots[lamp["id"]] = entry.runtime_data.get_channels(start, length)
            if (light := lights.get(lamp["id"])) is not None:
                states[lamp["id"]] = light.snapshot()
        if not snapshots:
            raise ServiceValidationError("Keine passende Lampe gefunden.")
        name = call.data[ATTR_NAME].strip() or "Speicher"
        scene = make_scene(name, snapshots, states)
        kept = [
            s for s in hass.data[DOMAIN]["scenes"]
            if not (s["name"] == name and set(s.get("channels", {})) == set(snapshots))
        ]
        hass.data[DOMAIN]["scenes"] = [*kept, scene]
        await save_scenes()

    async def handle_recall_scene(call: ServiceCall) -> None:
        scene_id = call.data[ATTR_SCENE_ID]
        scene = next(
            (s for s in hass.data[DOMAIN]["scenes"] if s["id"] == scene_id), None
        )
        if scene is None:
            raise ServiceValidationError("Szene nicht gefunden.")
        lights = hass.data[DOMAIN].get("lights", {})
        states = scene.get("state", {})
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            for lamp in merged_config(entry).get(CONF_LAMPS, []):
                values = scene["channels"].get(lamp["id"])
                start, length = lamp_span(lamp)
                if not values or len(values) != length:
                    continue
                light = lights.get(lamp["id"])
                if light is not None:
                    light.restore(values, states.get(lamp["id"]))
                else:
                    entry.runtime_data.set_channels(start, values)

    async def handle_delete_scene(call: ServiceCall) -> None:
        scene_id = call.data[ATTR_SCENE_ID]
        hass.data[DOMAIN]["scenes"] = [
            s for s in hass.data[DOMAIN]["scenes"] if s["id"] != scene_id
        ]
        await save_scenes()

    async def handle_recall_position(call: ServiceCall) -> None:
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        conf = merged_config(entry)
        position = next(
            (p for p in conf.get(CONF_POSITIONS, []) if p[POS_ID] == call.data[ATTR_POSITION_ID]),
            None,
        )
        if position is None:
            raise ServiceValidationError("Position nicht gefunden.")
        pan_16, tilt_16 = position_16bit(position)
        only = call.data.get(ATTR_LAMP_ID)
        for lamp in conf.get(CONF_LAMPS, []):
            if only and lamp["id"] != only:
                continue
            if not has_position(parse_layout_safe(lamp)):
                continue
            for channel, value in position_writes(lamp, pan_16, tilt_16):
                entry.runtime_data.set_channels(channel, [value])

    async def handle_aim(call: ServiceCall) -> None:
        """Kopf auf einen Bodenpunkt (Meter) richten; außerhalb wird auf den Rand begrenzt."""
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        conf = merged_config(entry)
        lamp = next(
            (l for l in conf.get(CONF_LAMPS, []) if l["id"] == call.data[ATTR_LAMP_ID]),
            None,
        )
        if lamp is None or not has_position(parse_layout_safe(lamp)):
            raise ServiceValidationError("Lampe ohne Pan/Tilt oder nicht gefunden.")
        room = Room.from_dict(conf.get(CONF_ROOM))
        mount = Mount.from_dict(lamp.get(LAMP_MOUNT))
        x, y = call.data[ATTR_X], call.data[ATTR_Y]
        if room.limit:
            x, y = clamp_point(room, mount, x, y)
        pan, tilt = aim_percent(mount, x, y)
        entry.runtime_data.free_lamps.discard(lamp["id"])
        for channel, value in position_writes(
            lamp, percent_to_16bit(pan), percent_to_16bit(tilt)
        ):
            entry.runtime_data.set_channels(channel, [value])

    async def handle_calibrate(call: ServiceCall) -> None:
        """Gemessenen Lichtpunkt (Meter) übernehmen und Pan/Tilt-Versatz daraus berechnen."""
        from .protocol import POSITION_MAX, parse_layout, read_position

        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        conf = merged_config(entry)
        lamps = [dict(l) for l in conf.get(CONF_LAMPS, [])]
        lamp = next((l for l in lamps if l["id"] == call.data[ATTR_LAMP_ID]), None)
        if lamp is None or not has_position(parse_layout_safe(lamp)):
            raise ServiceValidationError("Lampe ohne Pan/Tilt oder nicht gefunden.")
        layout = parse_layout(lamp["layout"])
        start = int(lamp["start_address"])
        current = read_position(layout, entry.runtime_data.get_channels(start, len(layout)))
        if current is None:
            raise ServiceValidationError("Aktuelle Position unbekannt.")
        mount_data = {**MOUNT_DEFAULTS, **lamp.get(LAMP_MOUNT, {})}
        pan_off, tilt_off = calibrate_offsets(
            Mount.from_dict(mount_data),
            current[0] / POSITION_MAX * 100,
            current[1] / POSITION_MAX * 100,
            call.data[ATTR_X],
            call.data[ATTR_Y],
        )
        lamp[LAMP_MOUNT] = {**mount_data, MOUNT_PAN_OFFSET: pan_off, MOUNT_TILT_OFFSET: tilt_off}
        _LOGGER.warning(
            "freeDMX %s kalibriert: Pan-Versatz %.1f°, Tilt-Versatz %.1f°",
            lamp.get("name"), pan_off, tilt_off,
        )
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, CONF_LAMPS: [lamp if l["id"] == lamp["id"] else l for l in lamps]},
        )

    async def handle_move(call: ServiceCall) -> None:
        """Pan/Tilt direkt in Prozent setzen (z. B. zum Kalibrieren).

        ohne_begrenzung: Raum-Begrenzung für diese Lampe kurz aussetzen (bis zum nächsten Neuladen
        bzw. bis ein Ziel über aim/Positionen/Szenen gesetzt wird).
        """
        entry = _target_entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        conf = merged_config(entry)
        lamp = next(
            (l for l in conf.get(CONF_LAMPS, []) if l["id"] == call.data[ATTR_LAMP_ID]),
            None,
        )
        if lamp is None or not has_position(parse_layout_safe(lamp)):
            raise ServiceValidationError("Lampe ohne Pan/Tilt oder nicht gefunden.")
        device = entry.runtime_data
        device.free_lamps.discard(lamp["id"])
        if call.data[ATTR_FREE]:
            device.free_lamps.add(lamp["id"])
        for channel, value in position_writes(
            lamp, percent_to_16bit(call.data[ATTR_PAN]), percent_to_16bit(call.data[ATTR_TILT])
        ):
            device.set_channels(channel, [value])

    async def handle_lamp_channel(call: ServiceCall) -> None:
        light = hass.data.get(DOMAIN, {}).get("lights", {}).get(call.data[ATTR_LAMP_ID])
        if light is None:
            raise ServiceValidationError("Lampe nicht gefunden.")
        try:
            light.set_override(call.data[ATTR_KANAL], call.data.get(ATTR_WERT))
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

    hass.services.async_register(
        DOMAIN, SERVICE_SET_LAMP_CHANNEL, handle_lamp_channel, schema=LAMP_CHANNEL_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_MOVE, handle_move, schema=MOVE_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_AIM, handle_aim, schema=AIM_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CALIBRATE, handle_calibrate, schema=AIM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_CHANNELS, handle_set_channels, schema=SET_CHANNELS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SCENE, handle_save_scene, schema=SAVE_SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALL_SCENE, handle_recall_scene, schema=SCENE_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_SCENE, handle_delete_scene, schema=SCENE_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALL_POSITION,
        handle_recall_position,
        schema=RECALL_POSITION_SCHEMA,
    )
    return True


def parse_layout_safe(lamp: dict) -> list:
    from .protocol import parse_layout

    return parse_layout(lamp["layout"])


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    conf = merged_config(entry)
    host = str(conf.get(CONF_HOST, DEFAULT_HOST))
    port = int(conf.get(CONF_PORT, DEFAULT_PORT))
    device = FreeDmxDevice(hass, host, port, conf.get(CONF_BIND) or None)
    try:
        await device.async_start()
    except OSError as err:
        raise ConfigEntryNotReady(
            f"freeDMX {host}:{port} kann nicht geöffnet werden: {err}"
        ) from err

    entry.runtime_data = device
    room_data = conf.get(CONF_ROOM)
    for lamp in conf.get(CONF_LAMPS, []):
        if (guard := make_guard(lamp, room_data)) is not None:
            device.guards.append(_skip_when_free(device, lamp["id"], guard))

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Eurolite",
        model="freeDMX AP",
        configuration_url=f"http://{host}",
    )

    from .runner import PatternRunner

    hass.data.setdefault(DOMAIN, {}).setdefault("runners", {})[entry.entry_id] = PatternRunner(
        hass, device, conf.get(CONF_LAMPS, []), room_data
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _skip_when_free(device: FreeDmxDevice, lamp_id: str, guard):
    def wrapped(buf, first, last):
        if lamp_id in device.free_lamps:
            return False
        return guard(buf, first, last)

    return wrapped


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runner = hass.data.get(DOMAIN, {}).get("runners", {}).pop(entry.entry_id, None)
        if runner:
            runner.stop()
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
