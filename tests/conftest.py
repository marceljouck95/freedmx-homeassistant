"""Gemeinsame Test-Konfiguration.

Die Home-Assistant-Tests (test_beat_ha.py) brauchen pytest-homeassistant-custom-component;
die nativen Tests laufen ohne Home Assistant und ohne dieses Plugin.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Mit dem HA-Testplugin ersetzt dessen hass-Fixture das Paket "custom_components" durch
# sein eigenes (testing_config). Deshalb die echte Integration vorher importieren,
# damit sie in sys.modules steht. Ohne Plugin (nativer Testlauf) passiert nichts.
try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:
    pass
else:
    import custom_components.freedmx  # noqa: F401
