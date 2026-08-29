# go-e Solar Charger (Home Assistant Integration)

Native Home-Assistant-Integration mit drei unabhaengigen Funktionen rund um
den go-e Charger:

1. **Auto Ladelimit** - stoppt den go-e, sobald das Elektroauto eine
   einstellbare Batterie-Ladung erreicht hat.
2. **PV-Ueberschuss-Freigabe** - schickt die Leistungswerte der Powerwall
   (Solar/Netz/Akku) an den go-e, aber erst, wenn der Akkustand der
   Powerwall eine einstellbare Schwelle erreicht hat.
3. **Guenstigstrom-Laden** (optional) - an Tagen mit schlechter
   Solar-Vorhersage wird die PV-Ueberschuss-Freigabe fuer den ganzen Tag
   pausiert und stattdessen im guenstigen Strompreis-Fenster mit Netzstrom
   geladen.

Auto, Powerwall und go-e sind bereits als Sensoren in Home Assistant
integriert - diese Integration liest nur davon. Alle Befehle an den go-e
(Stopp/Freigabe sowie die PV-Werte) gehen direkt an dessen lokale HTTP-API,
unabhaengig von Home Assistant.

Nur der go-e selbst ist fest verdrahtet (dessen lokale API). Fuers "Auto
Ladelimit" reicht jedes Elektroauto, das seinen Batteriestand (und
idealerweise Lade-/Verbunden-Status) als Sensor in Home Assistant
bereitstellt - egal ob ueber eine Fahrzeug-Integration, ein anderes
Ladegeraet oder sonstwie. Fuer "PV-Ueberschuss-Freigabe" reicht ebenso
jeder Akku/jedes Speichersystem mit Solar-, Netz-, Batterieleistungs- und
SoC-Sensor - die Powerwall ist hier kein Zwang, nur das, wofuer es gebaut
wurde. (Intern heissen manche Dateien/Klassen noch "zoe"/"controller" o.ae.,
weil das Projekt urspruenglich fuer eine Renault Zoe entstand - das ist
reine Namensgeschichte, die Logik selbst ist markenneutral.)

Eigenstaendiges Zusatzprojekt neben
[go-e-solar-charger](https://github.com/Twiederh/go-e-solar-charger), dem
eigenen Docker-Dashboard/Config-Tool mit direkter Powerwall-Gateway-
Anbindung. Dieses Projekt hier ist die HA-native Variante fuer alle, die
Auto/Powerwall/go-e schon in Home Assistant haben.

## Installation

### Als HACS Custom Repository (empfohlen)

1. HACS oeffnen -> die drei Punkte oben rechts -> **Benutzerdefinierte
   Repositories** (Custom repositories).
2. Als Repository-URL `https://github.com/Twiederh/ha-go-e-solar-charger`
   eintragen, als Kategorie **Integration** waehlen, mit "Hinzufuegen"
   bestaetigen.
3. "go-e Solar Charger" in HACS suchen und installieren.
4. Home Assistant neu starten.

### Manuell

1. Ordner `custom_components/go_e_solar_charger` aus diesem Repository in
   das `custom_components`-Verzeichnis deiner Home-Assistant-Konfiguration
   kopieren.
2. Home Assistant neu starten.

### Einrichtung

1. Einstellungen -> Geraete & Dienste -> Integration hinzufuegen -> "go-e
   Solar Charger" suchen.
2. Schritt "go-e Verbindung": IP-Adresse des go-e sowie optional dessen
   lokalen API-Key. Diese Verbindung wird von beiden Funktionen genutzt.
3. Schritt "Auto Ladelimit":
   - Sensor fuer den Batteriestand des Autos (bei dir z. B.
     `sensor.zoe_batterie_soc`)
   - Sensor/Binary-Sensor, der anzeigt, dass der go-e gerade laedt
   - optional: Sensor/Binary-Sensor "Fahrzeug verbunden" (ohne das wird
     der Ladestatus selbst als Anwesenheits-Ersatz verwendet)
   - Start-Ladelimit in %
4. Schritt "PV-Ueberschuss-Freigabe":
   - Sensor: Solarleistung der Powerwall (W)
   - Sensor: Netzleistung der Powerwall (W)
   - Sensor: Batterieleistung der Powerwall (W)
   - Sensor: Akkustand der Powerwall (%)
   - Start-Schwelle "PV-Freigabe ab Akkustand" in %
5. Schritt "Guenstigstrom-Laden" (optional - Felder leer lassen, um das
   Feature vorerst nicht zu nutzen):
   - Sensor: Solar-Vorhersage fuer morgen (kWh)
   - Sensor: aktueller Strompreis (numerischer Sensor, kein
     Binaer-/Target-Rate-Sensor)
   - Schalter: PV-Ueberschussladen am go-e selbst (der Schalter, den auch
     "PV-Ueberschuss-Freigabe" oben ansteuert, z. B.
     `switch.goe_wan_213832_fup`)
   - Vorhersage-Schwelle in kWh (Standard 30)
   - Preis-Schwelle in ct (Standard 20)

Alle drei Funktionen und die go-e-Verbindung lassen sich spaeter jederzeit
ueber "Konfigurieren" bei der Integration anpassen - auch um
"Guenstigstrom-Laden" nachtraeglich zu aktivieren.

## Erzeugte Entities

### Auto Ladelimit

- `number.<name>_auto_ladelimit` - Ladelimit in %, jederzeit im Dashboard
  aenderbar, bleibt nach einem Neustart erhalten.
- `switch.<name>_auto_ladelimit_aktiviert` - schaltet die automatische
  Ueberwachung an/aus.
- `sensor.<name>_auto_ladelimit_status` - Klartext-Status ("Laedt (62 % /
  Limit 80 %)", "Ladelimit erreicht - Laden gestoppt", "Kein Fahrzeug
  verbunden", ...).
- `button.<name>_laden_jetzt_stoppen` - manueller Sofort-Stopp, unabhaengig
  vom SoC. Praktisch, um die go-e-Verbindung zu testen, ohne auf das echte
  Limit zu warten.

(Diese Namen gelten fuer neu eingerichtete Integrationen. Bei einem
Update von einer aelteren Version bleiben die Entity-IDs bestehender
Entities unveraendert - `entity_id`s werden bei der Erstanlage vergeben
und danach nicht automatisch umbenannt, nur der angezeigte Name
aktualisiert sich.)

### PV-Ueberschuss-Freigabe

- `number.<name>_pv_freigabe_ab_akkustand` - Schwelle in %, jederzeit im
  Dashboard aenderbar, bleibt nach einem Neustart erhalten.
- `switch.<name>_pv_freigabe_aktiviert` - schaltet die Funktion an/aus.
- `sensor.<name>_pv_freigabe_status` - Klartext-Status ("PV-Werte gesendet
  (Akkustand 62 % >= 50 %)", "Akkustand 40 % < 50 % - keine PV-Freigabe an
  go-e", "Leistungswerte der Powerwall nicht verfuegbar", ...).
- `button.<name>_pv_jetzt_senden` - schickt die aktuell berechneten Werte
  sofort, praktisch zum Testen der go-e-Verbindung, ohne auf die naechste
  Sensor-Aenderung oder den Keep-Alive-Tick zu warten.

### Guenstigstrom-Laden

- `number.<name>_guenstigstrom_solar_schwelle` - Vorhersage-Schwelle in
  kWh, jederzeit im Dashboard aenderbar, bleibt nach einem Neustart
  erhalten.
- `number.<name>_guenstigstrom_preis_schwelle` - Preis-Schwelle in ct,
  ebenso persistent.
- `switch.<name>_guenstigstrom_aktiviert` - schaltet die Funktion an/aus.
  Beim Ausschalten waehrend eines aktiven Guenstigfensters wird die
  Kontrolle sofort zurueckgegeben (go-e-Zwangsladen beendet, PV-Schalter
  wieder an), statt bis zum naechsten Fenster zu warten.
- `sensor.<name>_guenstigstrom_status` - Klartext-Status ("Normaler Tag
  (Vorhersage 45.0 kWh >= 30 kWh)", "Guenstigstrom-Tag (...) - wartet auf
  Guenstigfenster", "Guenstigfenster aktiv - Laden erzwungen (...)",
  "Guenstigfenster aktiv, aber kein Fahrzeug verbunden", "Nicht
  konfiguriert - ...").
- `button.<name>_guenstigstrom_jetzt_testen` - sampelt die Solar-Vorhersage
  sofort neu (statt auf die taegliche Auswertungszeit zu warten) und wendet
  den aktuellen Fenster-Status direkt an - praktisch zum Testen der
  go-e-/Schalter-Verbindung.

Bestehende Installationen ohne die drei neuen Konfigurationsfelder
(Solar-Vorhersage, Strompreis, go-e-PV-Schalter) bleiben beim Update
unveraendert funktionsfaehig: das Feature startet inaktiv mit dem Status
"Nicht konfiguriert - bitte unter 'Konfigurieren' Solar-Vorhersage,
Strompreis und go-e-PV-Schalter angeben." und wird erst nach einem
Durchlauf durch "Konfigurieren" aktiv.

## Funktionsweise

### Auto Ladelimit

Reagiert auf Zustandsaenderungen der ausgewaehlten Sensoren (kein Polling).
Sobald der SoC das Limit erreicht oder ueberschreitet, waehrend geladen
wird, schickt die Integration `GET http://<go-e>/api/set?frc=1` (erzwingt
"Aus"). Sinkt der SoC wieder unter das Limit - weil du es hochgesetzt hast,
weil das Fahrzeug abgesteckt wurde, oder die Ueberwachung deaktiviert
wurde - wird `frc=0` (Neutral) gesendet und die normale go-e-Ladelogik
uebernimmt wieder. Die reine Entscheidungslogik steckt in `zoe_logic.py`,
frei von Home-Assistant-Importen, damit sie isoliert testbar ist.

### PV-Ueberschuss-Freigabe

Solange der Akkustand der Powerwall unter der eingestellten Schwelle
liegt, werden `pPv`, `pGrid` und `pAkku` als `0` an den go-e geschickt -
Sicherheits-Voreinstellung, damit der go-e nicht mit veralteten/falschen
PV-Werten weiterlaedt. Erreicht oder ueberschreitet der Akkustand die
Schwelle, werden die echten Momentanwerte per `GET
http://<go-e>/api/set?ids={"pPv":...,"pGrid":...,"pAkku":...}` gesendet.

Der go-e erwartet diese Werte mindestens alle 5 Sekunden aktualisiert -
kommt laenger nichts an, geht er davon aus, dass die PV-Quelle weg ist,
und pausiert das Laden als Sicherheitsmassnahme. Deshalb sendet die
Integration nicht nur bei jeder Aenderung der Quell-Sensoren, sondern
zusaetzlich alle 4 Sekunden erneut (`PV_PUSH_KEEPALIVE_INTERVAL_SECONDS`),
auch wenn sich die Werte gar nicht geaendert haben. Die reine
Entscheidungslogik steckt in `pv_logic.py`, frei von
Home-Assistant-Importen.

### Guenstigstrom-Laden

Zwei unabhaengige taegliche Rhythmen steuern dieses Feature:

1. Einmal am Abend (Standard: 20:30 Uhr) wird die aktuelle
   Solar-Vorhersage fuer "morgen" mit der Vorhersage-Schwelle verglichen
   und als "morgen wird ein Guenstigstrom-Tag" zwischengespeichert
   ("gelatcht"). Das ist notwendig, weil sich die Bedeutung eines
   "Vorhersage fuer morgen"-Sensors genau um Mitternacht auf den naechsten
   Tag verschiebt - ein Live-Zugriff waehrend des Guenstigfensters selbst
   waere also nicht sicher.
2. Der Strompreis-Sensor wechselt zwischen einem guenstigen und einem
   teuren Wert. Unterschreitet er die Preis-Schwelle, beginnt das
   Guenstigfenster; ueberschreitet er sie wieder, endet es. In dieser
   Konfiguration faellt der Beginn des Guenstigfensters praktischerweise
   mit Mitternacht zusammen - genau der Moment, in dem die am Vorabend
   gelatchte Entscheidung zur "heutigen" Entscheidung wird.

Ist "heute" laut dieser Entscheidung ein Guenstigstrom-Tag, passiert
Folgendes fuer den **ganzen Tag**: der go-e-eigene
PV-Ueberschussladen-Schalter wird ausgeschaltet, und die eigene
PV-Ueberschuss-Freigabe dieser Integration sendet ueberhaupt keine Werte
mehr (auch nicht die Sicherheits-Nullen) an den go-e. Zusaetzlich wird
**nur beim Betreten/Verlassen des Guenstigfensters** (nicht kontinuierlich)
`frc=On` gesetzt, solange ein Fahrzeug verbunden ist, und beim Verlassen
wieder auf `frc=Neutral` zurueckgesetzt - damit das unabhaengige
SoC-Ladelimit ("Auto Ladelimit") jederzeit weiterhin stoppen kann, ohne
dass sich beide Funktionen gegenseitig ueberschreiben. Ist "heute" kein
Guenstigstrom-Tag, bleibt alles wie gehabt (PV-Ueberschuss-Freigabe aktiv,
kein erzwungenes Laden).

Die reine Entscheidungslogik steckt in `cheap_logic.py`, frei von
Home-Assistant-Importen.

## Getestet, aber nicht an echter Hardware

Gegen einen echten go-e wurde das noch nicht ausprobiert - dafuer hat diese
Session keinen Netzwerkzugriff auf dein Heimnetz. Stattdessen:

- `custom_components/go_e_solar_charger/zoe_logic.py` hat einen
  eigenstaendigen, von Home Assistant unabhaengigen Test mit 13 Szenarien
  (Limit erreichen, Limit anheben waehrend gestoppt, Fahrzeug trennt,
  Deaktivieren waehrend gestoppt, SoC-Sensor kurzzeitig "unavailable", ...).
- `custom_components/go_e_solar_charger/pv_logic.py` ebenso, mit 7
  Szenarien (unter/auf/ueber Schwelle, deaktiviert, SoC nicht verfuegbar,
  einzelne Leistungswerte fehlen, ...).
- `custom_components/go_e_solar_charger/cheap_logic.py` ebenso, mit 20+
  Szenarien (Vorhersage ueber/unter Schwelle, Preis-Uebergaenge, taeglicher
  Rollover in beide Richtungen, Fenster-Ein-/Austritt mit/ohne verbundenes
  Fahrzeug, ...).
- `tests/test_integration.py` baut die komplette Integration (alle drei
  Funktionen) in einer echten (Test-)Home-Assistant-Instanz auf
  (`pytest-homeassistant-custom-component`), simuliert die
  Sensor-Uebergaenge und prueft, dass die richtigen Befehle an einen
  gemockten go-e gehen, inklusive Neustart (Limit/Schwelle/Aktiviert-
  Zustand bleiben erhalten), des kompletten
  Guenstigstrom-Tageszyklus (Abend-Latch -> Mitternacht-Rollover ->
  Fenster-Ende -> naechster Abend-Latch -> naechster Rollover zurueck)
  sowie des Falls einer bestehenden, noch nicht auf dieses Feature
  konfigurierten Installation.

Zum Ausfuehren:

```
pip install pytest pytest-asyncio pytest-homeassistant-custom-component homeassistant tzdata
PYTHONPATH=. pytest tests/
```

Vor dem produktiven Einsatz bitte einmal ueber die "Jetzt"-Buttons
verifizieren, dass der go-e tatsaechlich reagiert.
