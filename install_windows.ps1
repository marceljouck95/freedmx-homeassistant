# freeDMX-Integration in Home Assistant kopieren (Windows, PowerShell)
# Voraussetzung: Samba-Add-on in Home Assistant installiert und aktiv.
# Aufruf im Ordner, in dem die entpackte freedmx-Datei liegt:
#   powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
# Optional: anderer Netzwerkpfad, z. B. \\192.168.178.68\config

param(
    [string]$HaConfig = "\\192.168.178.68\config"
)

$ErrorActionPreference = "Stop"

$source = Join-Path $PSScriptRoot "custom_components\freedmx"
if (-not (Test-Path $source)) {
    Write-Error "Quellordner nicht gefunden: $source"
    exit 1
}

if (-not (Test-Path $HaConfig)) {
    Write-Error "Der Konfigurationsordner $HaConfig ist nicht erreichbar. Samba-Add-on und Anmeldung prüfen."
    exit 1
}

$targetParent = Join-Path $HaConfig "custom_components"
if (-not (Test-Path $targetParent)) {
    New-Item -ItemType Directory -Path $targetParent | Out-Null
}

$target = Join-Path $targetParent "freedmx"
if (Test-Path $target) {
    # Sicherung AUSSERHALB von custom_components, sonst lädt Home Assistant sie als zweite Integration
    $backup = Join-Path $HaConfig "freedmx_sicherung"
    if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
    Copy-Item $target $backup -Recurse
    Remove-Item $target -Recurse -Force
    Write-Host "Vorhandene Version gesichert nach: $backup"
}
$old = Join-Path $targetParent "freedmx.alt"
if (Test-Path $old) { Remove-Item $old -Recurse -Force; Write-Host "Alte Sicherung freedmx.alt entfernt." }

Copy-Item -Path $source -Destination $targetParent -Recurse -Force
Write-Host "Integration liegt jetzt unter: $target"

# Die Karte liegt im Integrationsordner und wird von Home Assistant unter /freedmx_static/ ausgeliefert.
Write-Host "Als Nächstes Home Assistant neu starten (Einstellungen > System > Neu starten)."
