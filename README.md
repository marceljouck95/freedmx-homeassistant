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
2. Danach Frames aus 512 Kanälen (1536 Byte), aufgeteilt in 7 UDP-Pakete. Das Protokoll (wie QLC+) sendet dauerhaft alle 40 ms. Die Integration sendet nur bei Änderungen, höchstens im eingestellten Sendetakt (Standard 50 ms, wählbar 40/50/100 ms unter Optionen → Verbindung), und frischt ohne Änderung alle 0,5 s auf.
3. Kommt 2 s lang kein `F0 64`, gilt das Gerät als getrennt und der Handshake wird wiederholt.
4. Beim Entladen der Integration wird `AC 00` gesendet.

## Tests
`python3 tests/test_native.py` prüft die Kodierung gegen die Beispiele im Protokoll
und den Sender gegen ein simuliertes freeDMX (ohne Home Assistant).

## Neu in 0.8.0

- **Verbinden / Trennen:** Schalter „Verbindung“. Getrennt sendet Home Assistant nichts mehr an das freeDMX (frei für andere Apps); die Werte bleiben gemerkt. Karte `custom:freedmx-verbindung-card` mit Taste, optional „beim Öffnen der Seite verbinden“ und „beim Verlassen trennen“. Die Kanäle-Karte hat dieselbe Taste.
- **Jeder Wert einzeln:** In der Kanäle-Karte wählt man die Funktion und daneben jeden einzelnen Wert innerhalb der Funktion (zusätzlich Zahl, −/+ und Regler für 0–255).
- **Gruppe:** Taste „Gruppe“ in der Kanäle-Karte – Einstellungen und Ein/Aus gelten für alle gleichen Lampen (oder die im Editor gewählten).
- **Speicherplätze:** Position, Farbe und alle Kanäle unter einem Namen sichern, abrufen und löschen (für eine Lampe, die Gruppe oder alle Lampen). Dienst `freedmx.save_scene` mit optionalem `lamp_ids`.

## Neu in 0.9.0: Beat-Sync mit Music Assistant

Licht und Moving Heads laufen automatisch im Takt der Musik, die über Music Assistant spielt. Tippen ist nicht nötig.

- **Tempo:** zum laufenden Titel automatisch über die freie Deezer-API. Hat Deezer kein Tempo, misst die Integration es selbst aus der 30-Sekunden-Hörprobe (ffmpeg + numpy, beides in Home Assistant enthalten). Jedes Tempo wird je Titel gespeichert.
- **Takt:** kommt aus der Wiedergabeposition des Players. Start, Pause, Spulen und Titelwechsel werden übernommen, dazwischen läuft der Takt gleichmäßig weiter.
- **Effekte:** Puls, Blitz, Farbwechsel, Puls + Farbe, Lauflicht, Wechsel. Sie wirken auf ausgewählte Lampen mit Dimmer oder RGB.
- **Farben:** feste Paletten oder bis zu 8 eigene Farben.
  Die Palette gilt (Schalter „Palette auch bei Puls, Blitz …“, Standard an) bei allen Effekten; aus = eigene Lampenfarbe. Je Lampentyp:
  - **RGB-Lampen** (z. B. LED Bar): Farbe direkt.
  - **Futurelight DMH-40:** nächste Farbrad-Farbe (Kanal 4), ungesättigte Farben = Weiß. Das Farbrad wechselt höchstens alle 250 ms; der Dimmer pulst im Takt.
  - **Eurolite FE-1500:** DMX-Betriebsart, nächste LED-Farbe (Kanal 2), Puls über LEDs an/aus (kein Dimmer), Laser rot/grün passend zur Farbe im Takt (abschaltbar), Drehung leicht an.
  DMH-40 und FE-1500 werden auch mit „Eigenem Layout“ erkannt.
- **Geschwindigkeit:** Effekt doppelt so schnell, jeder Beat, alle 2 Beats oder jeder Takt. Ein Muster-Durchlauf der Moving Heads dauert 1–32 Beats.
- **Moving-Head-Tempo:** Die Kopfgeschwindigkeit (Futurelight DMH-40, Kanal 3: 0 = schnellste, 225 = langsamste; Werte darüber werden nie geschrieben) folgt dem Tempo der Musik: etwa 70 BPM und langsamer = langsam, 128 BPM = „Standard-Geschwindigkeit“, 175 BPM und schneller = schnell, dazwischen glatt. Die Muster-Durchläufe laufen ohnehin im Takt (1–32 Beats). In der Karte unter „Geschwindigkeit“: Schalter „Moving-Head-Tempo folgt dem Beat“ und Regler „Standard-Geschwindigkeit“ (1–100 %, Standard 50; gilt dauerhaft, wenn der Schalter aus ist). Einstellungen `mh_speed_auto` und `mh_speed` im Dienst `freedmx.beat_settings`. Köpfe, die du gerade von Hand bedienst, oder ein selbst gesetzter Kanal 3 bleiben unberührt; Werte werden nur bei Änderung gesendet.
- **Automatik:** gewählte Lampen gehen mit der Musik an und nach 20 s Stille wieder aus.
- **Feinjustage (selten nötig):** Lautsprecher-Versatz in ms, ±¼ Beat, halbes/doppeltes Tempo, „Jetzt ist 1“, Tippen als Notlösung.

Neue Entitäten: `sensor.…_beat_tempo` (BPM, alle Infos als Attribute), `switch.…_beat_sync`, `select.…_beat_effekt`, `select.…_beat_quelle`, `button.…_beat_tippen`, `button.…_beat_ausrichten`.
Neue Dienste: `freedmx.beat_settings`, `freedmx.beat_bpm`, `freedmx.beat_align`, `freedmx.beat_tap`.
Neue Karte: `custom:freedmx-beat-card` (in derselben Datei wie die Raum-Karte, Einstellungen im Karten-Editor).

**Grenzen:** Der Takt wird aus Tempo und Wiedergabeposition berechnet, nicht aus dem Ton selbst. Bei Titeln mit langem Intro ohne Schlag oder mit wechselndem Tempo kann er leicht danebenliegen. Dafür gibt es ±¼ Beat; die Korrektur wird je Titel gemerkt. Beim Sendetakt „Normal“ (20 Bilder/s) sind Effekte auf ±25 ms genau.
