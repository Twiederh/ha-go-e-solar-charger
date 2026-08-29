# Zoe Ladelimit (go-e)

Home-Assistant-Integration, die den go-e Charger stoppt, sobald die
Renault Zoe eine einstellbare Batterie-Ladung erreicht hat. Powerwall und
go-e sind bereits als Sensoren in Home Assistant integriert - diese
Integration liest nur davon (Ladezustand der Zoe, Lade-/Verbunden-Status
des go-e). Der Stopp-Befehl selbst geht direkt an den go-e (lokale HTTP-
API), unabhaengig von Home Assistant.

Eigenstaendiges Zusatzprojekt neben
[go-e-solar-charger](https://github.com/Twiederh/go-e-solar-charger), das
weiterhin unveraendert fuer die PV-Ueberschussladung zustaendig ist.

## Installation

1. Ordner `custom_components/zoe_charge_limit` in das `custom_components`-
   Verzeichnis deiner Home-Assistant-Konfiguration kopieren (oder als
   HACS-Custom-Repository hinzufuegen, `hacs.json` ist dabei).
2. Home Assistant neu starten.
3. Einstellungen -> Geraete & Dienste -> Integration hinzufuegen -> "Zoe
   Ladelimit (go-e)" suchen.
4. Im Einrichtungsdialog auswaehlen:
   - Sensor fuer den Batteriestand der Zoe (bei dir vermutlich
     `sensor.zoe_batterie_soc`)
   - Sensor/Binary-Sensor, der anzeigt, dass der go-e gerade laedt
   - optional: Sensor/Binary-Sensor "Fahrzeug verbunden" (ohne das wird
     der Ladestatus selbst als Anwesenheits-Ersatz verwendet)
   - IP-Adresse des go-e sowie optional dessen lokalen API-Key
   - Start-Ladelimit in %

## Erzeugte Entities

- `number.<name>_ladelimit` - Ladelimit in %, jederzeit im Dashboard
  aenderbar, bleibt nach einem Neustart erhalten.
- `switch.<name>_aktiviert` - schaltet die automatische Ueberwachung an/aus.
- `sensor.<name>_status` - Klartext-Status ("Laedt (62 % / Limit 80 %)",
  "Ladelimit erreicht - Laden gestoppt", "Kein Fahrzeug verbunden", ...).
- `button.<name>_jetzt_stoppen` - manueller Sofort-Stopp, unabhaengig vom
  SoC. Praktisch, um die go-e-Verbindung zu testen, ohne auf das echte
  Limit zu warten.

## Funktionsweise

Reagiert auf Zustandsaenderungen der ausgewaehlten Sensoren (kein Polling).
Sobald der SoC das Limit erreicht oder ueberschreitet, waehrend geladen
wird, schickt die Integration `GET http://<go-e>/api/set?frc=1` (erzwingt
"Aus"). Sinkt der SoC wieder unter das Limit - weil du es hochgesetzt hast,
weil das Fahrzeug abgesteckt wurde, oder die Ueberwachung deaktiviert
wurde - wird `frc=0` (Neutral) gesendet und die normale go-e-Ladelogik
uebernimmt wieder. Die reine Entscheidungslogik steckt in `logic.py`, frei
von Home-Assistant-Importen, damit sie isoliert testbar ist.

## Getestet, aber nicht an echter Hardware

Gegen einen echten go-e wurde das noch nicht ausprobiert - dafuer hat diese
Session keinen Netzwerkzugriff auf dein Heimnetz. Stattdessen:

- `custom_components/zoe_charge_limit/logic.py` hat einen eigenstaendigen,
  von Home Assistant unabhaengigen Test mit 13 Szenarien (Limit erreichen,
  Limit anheben waehrend gestoppt, Fahrzeug trennt, Deaktivieren waehrend
  gestoppt, SoC-Sensor kurzzeitig "unavailable", ...).
- `tests/test_integration.py` baut die Integration komplett in einer
  echten (Test-)Home-Assistant-Instanz auf (`pytest-homeassistant-custom-
  component`), simuliert die Sensor-Uebergaenge und prueft, dass die
  richtigen `frc`-Befehle an einen gemockten go-e gehen, inklusive Neustart
  (Limit/Aktiviert-Zustand bleibt erhalten).

Zum Ausfuehren:

```
pip install pytest pytest-asyncio pytest-homeassistant-custom-component homeassistant tzdata
PYTHONPATH=. pytest tests/
```

Vor dem produktiven Einsatz bitte einmal ueber den "Jetzt stoppen"-Button
verifizieren, dass der go-e tatsaechlich reagiert.
