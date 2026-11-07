# Eurolite freeDMX AP für Home Assistant

Hinweis: privates Projekt, ohne Gewähr. Laser- und Moving-Head-Nutzung auf eigene Verantwortung.

Steuert DMX-Lampen über den Eurolite freeDMX AP. Die Integration spricht das native
freeDMX-Protokoll per UDP, kein Art-Net nötig. Einrichtung komplett über die Oberfläche.

Protokoll: `freedmxprotocol.txt` aus dem QLC+-Plugin von Rodolphe Dejeunes (CC BY-SA 4.0).
Die Angaben stammen nicht von Steinigke/Eurolite und sind laut Dokument nicht validiert.

## Voraussetzung
- Home Assistant ist mit dem WLAN des freeDMX verbunden (Standard-SSID `freeDMX_AP_xxxxxx`, IP `192.168.4.1`),
  oder das freeDMX hängt im Heimnetz und ist per IP erreichbar.
- Die Eurolite-App und die Integration sollten nicht gleichzeitig senden.

## Installation (HACS)
1. HACS → oben rechts ⋮ → **Benutzerdefinierte Repositories** → URL dieses Repositorys, Typ **Integration**.
2. „Eurolite freeDMX AP“ herunterladen, Home Assistant neu starten.
3. Einstellungen → Geräte & Dienste → Integration hinzufügen → „Eurolite freeDMX AP“.
4. Karte: Ressource `/freedmx_static/freedmx-raum-card.js` (JavaScript-Modul) eintragen,
   dann `type: custom:freedmx-raum-card`. Die Karte wird mit der Integration aktualisiert.

Updates: HACS zeigt neue Versionen an → **Aktualisieren** → Neustart.

## Einrichtung
- **IP-Adresse** (Standard `192.168.4.1`) und **Port** (Standard `10100`).
- **Lokale IP** ist optional. Nur nötig, wenn Home Assistant mehrere Netzwerkadapter hat.

Lampen legst du unter **Konfigurieren** an:
- **Lampe hinzufügen:** Name, DMX-Startadresse, Kanalbelegung
- **Lampe bearbeiten / entfernen**
- **Verbindung:** IP, Port, lokale IP

Jede Lampe erscheint als Gerät mit Licht-Entität (Ein/Aus, Helligkeit, Farbe, Übergang).
Die Lampe ist „nicht verfügbar“, solange das freeDMX nicht antwortet.

## Kanalbelegung
Vorlagen: Dimmer, RGB, RGBW, Dimmer + RGB, Dimmer + RGBW, 7-Kanal-PAR.
Eigene Belegung kommagetrennt in der Reihenfolge der Lampe:

| Zeichen | Bedeutung |
|---|---|
| `D` | Dimmer (Helligkeit) |
| `R` `G` `B` `W` | Farbkanäle |
| `X` | ungenutzt (immer 0) |
| `0`–`255` | fester Wert, z. B. für Modus- oder Strobe-Kanal |

Beispiel 8-Kanal-PAR mit Dimmer, RGBW, Strobe aus, Modus 0, Speed 0: `D,R,G,B,W,0,0,0`

Ohne `D` wird die Helligkeit über die Farbkanäle geregelt.

## Dienst `freedmx.set_channels`
Setzt Rohwerte ab einem Kanal, z. B. für Nebelmaschinen:
```yaml
action: freedmx.set_channels
data:
  channel: 20
  values: [255, 0]
```

## Ablauf des Protokolls (umgesetzt)
1. Handshake `E5 39 60 00`, bis das Gerät mit `A7` oder `F0 64` antwortet.
2. Danach alle 40 ms ein Frame aus 512 Kanälen (1536 Byte), aufgeteilt in 7 UDP-Pakete.
3. Kommt 2 s lang kein `F0 64`, gilt das Gerät als getrennt und der Handshake wird wiederholt.
4. Beim Entladen der Integration wird `AC 00` gesendet.

## Tests
`python3 tests/test_native.py` prüft die Kodierung gegen die Beispiele im Protokoll
und den Sender gegen ein simuliertes freeDMX (ohne Home Assistant).
