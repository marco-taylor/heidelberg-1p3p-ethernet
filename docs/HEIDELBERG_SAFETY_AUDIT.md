# Heidelberg Ethernet: Firmware-Sicherheitskorrekturen

Stand: 18.11.2026. Änderungsentwurf, keine Freigabe zum Flashen oder zum Betrieb an Leistungskreisen.

## Repository und Grundlage

Vor Änderungen wurde die Zuordnung geprüft: `origin` ist `marco-taylor/heidelberg-1p3p-ethernet`, Repository-ID 1462364686. Das Ziel ist **nicht leer**. Sein unveränderter Hauptbranch `main` zeigt auf `1e6654ddc7d15faa034e8e2980d6c3aef9f9d238`, identisch mit `jetronic18s/heidelberg-1p3p` v1.2.0. Es werden keine Historien oder Tags importiert. Änderungen liegen ausschließlich auf `fix/safety-restart-feedback-v1.2.0`.

Ausgangspunkt ist der [Sicherheitsbericht aus evcc PR #4](https://github.com/marco-taylor/evcc-heidelberg-1p3p/pull/4). Die ursprünglichen Funde gelten für unveränderte v1.2.0, nicht als fortbestehende Aussage über diesen Entwurf. Originaldatei `src/switch.cpp`: SHA256 `6ecba1351b0fde8af0287aa05d3c2066688df4386e3f472b13f7073a06874fdf`.

## Nachgewiesene Fehler und Korrekturen

| Fund | Konkrete Fundstelle im Entwurf | Korrektur und Regression |
| --- | --- | --- |
| F01: erzwungener Reset nach Stopp-Timeout | `src/switch.cpp`, `PhaseSwitch::handleRestart()` | Timeout protokolliert die Blockade und wiederholt Stoppbefehle. Kein Reset ohne frische Input-Register 5–8 mit Status 10 und drei Nullströmen sowie erfolgreiche Strom-/Sperrbefehle. Alternative nur bei bereits ausgeschalteten Ausgängen und 100 ms bestätigter Aus-Rückmeldung. `restart_timeout`, `restart_read_error`, `restart_missing_master`, `restart_late_stop`. |
| F02: Web-/OTA-/WLAN-Timer umgeht Stoppbarriere | `src/pages.cpp`, `scheduleRestart()`, `handleUpdatePost()` | Kein direktes `esp_restart()` und kein Zwangs-Ausweichtimer. Callback fordert ausschließlich dieselbe gesicherte Neustartsequenz an. OTA verlangt vor `esp_ota_begin()` bestätigten Stopp oder bestätigte ausgeschaltete Ausgänge; andernfalls HTTP 409 vor dem Löschen. Timerfehler erzeugen keinen direkten Reset. `web_restart_active`, `web_timer_create_error`, `web_timer_start_error`, `web_null_controller`, `ota_active_guard`, `ota_lock_error`. |
| F03: vorzeichenloser Entprell-Unterlauf | `include/switch.h`, `FeedbackDebounce`; `src/switch.cpp`, `persistsFor()` | Aktivzustand und Zeitstempel getrennt; eine Uhrabfrage pro Aufruf, Differenz über uint32-Überlauf korrekt. Gerade Zeit, Nullzeit, Unterbrechung und Überlauf getestet. `debounce_even`, `debounce_zero`, `debounce_wrap`, `debounce_interrupted`. |
| F04: falsche einzelne Schützrückmeldung akzeptiert | `src/switch.cpp`, `feedbackMatches()`, `loop()`, `reportedPhases()` | Running vergleicht mit der angeforderten Phase, Übergänge mit der zuvor bestätigten Phase. Neustart prüft ebenfalls die bestätigte Stellung. Ausschalten verlangt stabile Aus-Rückmeldung; direkt vor Einschalten erneute Rückmeldeprüfung. Kein automatischer Korrekturwechsel unter Last. `wrong_feedback`, `feedback_reappears_off`, `phase_only_after_confirmation`. |
| F05: Sperrbefehlfehler als Erfolg behandelt | `src/switch.cpp`, `writeRestartStop()` | Erfolg erst nach beiden bestätigten Schreibtransaktionen. Lesefehler, Status ungleich 10 und Strom auf einer beliebigen Phase verhindern Reset. `restart_lock_error`, `restart_lock_retry`, `restart_current_L2`, `restart_current_L3`, `restart_unlocked_zero`. |
| F06: Entsperr-Timeout behauptet Running | `src/switch.cpp`, `loop()`, Zustand `Unlocking` | Ohne gültige Entsperrrückmeldung im Zustand verbleiben, Wiederholung überwachen; kein Erfolgstimestamp durch Timeout. Nur Wallboxstatus 2–7 beendet Unlocking. `unlock_requires_confirmation`, `unlock_invalid_status`. |

## Absichtlich geänderte Wiederanlauf- und Protokollregeln

Diese Änderungen betreffen Sicherheitsverhalten und müssen vor einem Merge ausdrücklich geprüft werden.

- Konstruktor startet in `AwaitingStart` mit beiden Ausgängen aus und bestätigter Phase 0. Kaltstart, Brownout, Watchdog, Panic sowie ungültiger RTC-Cache ziehen kein Schütz automatisch an. Das betrifft auch den ersten Start nach Installation und die Migration von v1.2.0.
- Zum erneuten Start ist eine bewusste 1P-/3P-Auswahl über die bestehende Web-Bedienung nötig, nachdem beide Rückmeldungen aus sind und der Modbus-Master läuft. Die normale Ausschaltpause und anschließende Validierung bleiben bestehen. Das ist eine Software-Bediensperre, keine physische Freischaltung und keine Authentifizierungsgrenze der Weboberfläche.
- Ein kontrollierter Software-Reset darf nur die **zuvor bestätigte** Phase wieder anfordern: RTC-Magic HEC2, CRC, Resetgrund `ESP_RST_SW` und gespeichertes `safeResume` sind nötig. Dieses Flag entsteht nur nach bestätigter Sperre und Nullstrom. Ein vorgemerkter Wechsel wird nicht ungeprüft wieder aufgenommen. Ein Fault setzt keine automatische Wiederanlauffreigabe.
- Nach erlaubtem Wiederanlauf werden zuerst 0 A und danach Entsperren geschrieben. Während dieses initialen Handovers eingehende Cache-Stromwerte bleiben bei 0 A. Danach darf evcc wieder eigene Ladebefehle geben. Eine dauerhafte Ladesperre nach bewusster Freigabe ist damit nicht zugesichert.
- Holding-Register 4 meldet jetzt die letzte erfolgreich über Hilfskontakt und Spannungsmuster bestätigte Phase 1/3, solange das zugehörige exklusive Kontaktmuster aktuell passt. Bei unbestätigter, ausgeschalteter oder fehlerhafter Stellung: 0. Es ist kein vorauslaufender Auftragswert mehr. Keine unabhängige elektrische Messung der tatsächlichen Lastphasen.
- Phasenaufträge bei OTA, Neustart oder Fault werden abgewiesen; Modbus darf `AwaitingStart` nicht aufheben. Wo das lokale Phasenregister aktiv ist, lautet die Busy-Ausnahme 6. Ungültige Aufträge bleiben Fehler. Ein regulärer Phasenwechsel darf weiterhin Folgeaufträge vormerken.
- evcc PR #2 akzeptiert 1/3 und behandelt 0 als nicht verfügbar. Daher ist keine Treiberänderung nötig; trotzdem handelt es sich um eine Protokolländerung. Wenn evcc seine Fähigkeiten während `AwaitingStart` erkennt, kann die Phasenumschaltfähigkeit fehlen. Nach bewusstem Start und bestätigter Stellung evcc erneut initialisieren. Das wurde nicht am produktiven Container ausgeführt.
- OTA hält den vorher aktiven Kontaktor während Stoppbestätigung und Upload eingeschaltet. Cached evcc-Schreibzugriffe lösen dabei keinen Wechsel aus. Ein abgebrochener Upload beendet den Cachemodus nach bisherigem Verfahren; danach dürfen neue evcc-Befehle wieder wirken. Ein Upload ist keine dauerhafte manuelle Ladesperre.

## Automatisierte Nachweise

`test/host/safety_sim.py` kompiliert die tatsächlichen C++-Funktionen aus `src/switch.cpp` und `scheduleRestart()` aus `src/pages.cpp` mit `-Wall -Wextra -Werror`. GPIO, Uhr, Modbus, Timer, CRC-Umgebung und Reset werden ersetzt. Es wird kein Gerät geöffnet. Die OTA-Reihenfolge vor dem Löschen ist zusätzlich eine statische Strukturprüfung; der vollständige HTTP-/Flash-Stack wird nicht simuliert.

**55 Simulationen bestanden, 0 erwartete Fehler, 0 übersprungene Tests.** Alle 28 ursprünglichen Fälle bleiben enthalten. Die acht früher erwarteten Anforderungsverletzungen sind nun harte bestandene Regressionen. Weitere 27 Fälle prüfen unter anderem aktive Neustarts während Umschaltung/Entsperren, jede Stromphase, fehlenden Master, spätere erfolgreiche Stoppbestätigung, Timerfehler, Reset-Wiederanlaufsperren, manuelle Freigabe und gleichzeitig eintreffende Stromvorgaben beim Boot-Handover.

Ausführen ohne Hardware:

```sh
python3 test/host/safety_sim.py
```

CI führt diese Tests vor dem bestehenden vollständigen ESP-IDF-Build aus. Branch-/PR-Ereignisse erstellen keine Releases; der vorhandene Release-Schritt reagiert ausschließlich auf Versions-Tags. Es werden keine Tags erstellt oder gepusht.

Vollständiger Build: ESP-IDF v5.5.6, ESP32, 4-MB-Partitionierung, einschließlich eingebetteter Weboberfläche. Lokaler vollständiger Build erfolgreich; App-Größe 1.065.728 Byte (0x104300), kleinste OTA-Partition 0x140000, 19 % frei. Alle drei Produktionsdateien im Build wurden bytegleich mit dem Entwicklungsbranch verglichen. Containerbasis: `espressif/idf@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84`. Ein erfolgreicher Build ist kein Hardware-Sicherheitsnachweis und keine Zusage bitidentischer Binärdateien.

## Unveränderte Grenzen und notwendige Hardwareprüfungen

1. **Brownout/Watchdog/Absturz lassen sich nicht durch eine Software-Stoppbarriere verhindern.** Ausgänge können bereits im Reset oder bei Versorgungsausfall abfallen, während Strom fließt. Die Wiederanlaufsperre verhindert anschließend automatisches Einschalten, nicht das vorherige physische Öffnen unter Last.
2. `enterSafetyFault()` schaltet beide Ausgänge weiterhin unmittelbar ab. Das ist die vorhandene Notreaktion bei widersprüchlichen/fehlenden Rückmeldungen oder fehlgeschlagener Einschaltbestätigung. Es gibt dabei keine garantierte Nullstrombestätigung. Eine andere Notreaktion benötigt ein abgestimmtes elektrisches Schutzkonzept und gesonderte Prüfung.
3. Boot-ROM, GPIO2/16, interne Pull-Widerstände, DT-R002-DEV-Ausgangsstufe und Schützspulen mit Oszilloskop prüfen. Vorladen des Aus-Pegels vor Aktivierung des GPIO-Ausgangs reduziert nur ein Anwendungssoftwarefenster. Reset-/Brownout-Glitches sind damit nicht ausgeschlossen.
4. Mechanische und elektrische Verriegelung, zwanggeführte Hilfskontakte, klebende Kontakte und Rückmeldeverdrahtung verifizieren. Zwei logische Eingänge beweisen keine sichere Hauptkontaktstellung. 100-ms-Entprellung und 2-s-Ausschaltpause elektrisch prüfen.
5. Modbus-Ströme mit unabhängiger Messung vergleichen: Auflösung, Messverzug, Aktualität, Status 10, alle drei Phasen sowie gespeicherte Ladefreigabe/Failsafe-Werte beim Wallbox-Wiederanlauf. Ein gemeldeter ganzzahliger Nullwert beweist keine stromlose Leitung.
6. Kontrollierte Neustarts/OTA während Last erst in einem fachgerecht abgesicherten Prüfaufbau messen: Stopp, alle Ströme null, Kontaktabfall, Neustart und Wiederfreigabe zeitlich korrelieren. Keine Netzlastversuche wurden hier ausgeführt.
7. FreeRTOS-Task-Scheduling, Buslast, verlorene Antworten, Timeout-Dauer, OTA-Schreibpausen und Hardware-Watchdogs unter realer Last prüfen. Die deterministische Hostsimulation ist keine vollständige Nebenläufigkeits- oder ESP32-Emulation.
8. Netzwerkzugriff auf Web-Bedienung/OTA und evcc organisatorisch absichern. Die manuelle Startbedingung ist kein Schutz vor beliebigen autorisierten oder ungeschützten Web-Befehlen. Eine physische Wartungssperre wird nicht ersetzt.

Lizenz- und Urheberrechtshinweise des Originalprojekts bleiben unverändert. Kein Flashen, kein Hardwarezugriff, keine Änderung am produktiven Unraid-Container, kein Merge und keine Veröffentlichung einer Firmware im Rahmen dieser Arbeit.
