"""Konstanten für die freeDMX-Integration."""

from .protocol import DEFAULT_HOST, DEFAULT_PORT

DOMAIN = "freedmx"

CONF_BIND = "bind"
CONF_LAMPS = "lamps"
CONF_POSITIONS = "positions"

# Lampen-Felder
LAMP_ID = "id"
LAMP_NAME = "name"
LAMP_START = "start_address"
LAMP_PRESET = "preset"
LAMP_LAYOUT = "layout"
LAMP_CUSTOM = "custom_layout"

DEFAULT_NAME = "freeDMX AP"

PRESET_CUSTOM = "custom"
PRESETS: dict[str, str] = {
    "dimmer": "D",
    "rgb": "R,G,B",
    "rgbw": "R,G,B,W",
    "drgb": "D,R,G,B",
    "drgbw": "D,R,G,B,W",
    "par7": "D,R,G,B,0,0,0",
}

SERVICE_SET_CHANNELS = "set_channels"
SERVICE_SAVE_SCENE = "save_scene"
SERVICE_RECALL_SCENE = "recall_scene"
SERVICE_DELETE_SCENE = "delete_scene"
SERVICE_RECALL_POSITION = "recall_position"
SERVICE_AIM = "aim"
SERVICE_CALIBRATE = "calibrate"
SERVICE_MOVE = "move"
ATTR_PAN = "pan"
ATTR_TILT = "tilt"
ATTR_FREE = "ohne_begrenzung"
CONF_ROOM = "room"
ATTR_X = "x"
ATTR_Y = "y"
ATTR_NAME = "name"
ATTR_SCENE_ID = "scene_id"
ATTR_POSITION_ID = "position_id"
ATTR_LAMP_ID = "lamp_id"

POS_ID = "id"
POS_NAME = "name"
POS_PAN = "pan"    # 0-100 %, wird auf 16 Bit abgebildet
POS_TILT = "tilt"  # 0-100 %

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.scenes"
ATTR_CHANNEL = "channel"
ATTR_VALUES = "values"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

__all__ = [
    "ATTR_CHANNEL",
    "ATTR_CONFIG_ENTRY_ID",
    "ATTR_VALUES",
    "CONF_BIND",
    "CONF_LAMPS",
    "CONF_POSITIONS",
    "DEFAULT_HOST",
    "DEFAULT_NAME",
    "DEFAULT_PORT",
    "DOMAIN",
    "LAMP_CUSTOM",
    "LAMP_ID",
    "LAMP_LAYOUT",
    "LAMP_NAME",
    "LAMP_PRESET",
    "LAMP_START",
    "PRESETS",
    "PRESET_CUSTOM",
    "SERVICE_SET_CHANNELS",
]
