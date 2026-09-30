# LingerLens Benutzerhandbuch

[Dokumentation](../README.md) · Stand 0.1.3 · Aktualisiert 2026-09-30

## Start

Der Windows-Installationsordner ist standardmäßig `%LOCALAPPDATA%/Programs/lingerlens`; Benutzerdaten liegen separat unter `%APPDATA%/LingerLens`. Ab 0.1.1 lässt sich der Ordner im Installationsassistenten auswählen.

Ein neues Profil enthält keine konfigurierten Modelle, API-Schlüssel oder importierten Cookies. Fügen Sie eigene Verbindungen in den Modelleinstellungen hinzu. Updates und Neuinstallationen behalten vorhandene lokale Benutzerdaten; alte Einstellungen auf einem Entwicklungsrechner stammen daher nicht zwangsläufig aus dem Installer. Verwenden Sie für Aufnahmen ein neues Datenverzeichnis und nehmen Sie weder Benutzerdaten noch deren Sicherungen in Veröffentlichungspakete auf.

Buildziele sind Windows x64 und macOS arm64/x64. Verfügbare Pakete stehen unter [Releases](https://github.com/Antiranger/LingerLens/releases). Windows: EXE ausführen. Mac: passendes DMG öffnen und die App in Programme ziehen. Windows-Pakete sind unsigniert. Mac-Pakete sind ad hoc signiert, jedoch ohne Apple Developer ID und Notarisierung. Prüfe `SHA256SUMS.txt`.

Electron, Python, FFmpeg/ffprobe, yt-dlp, Schriftarten und das japanische Wörterbuch sind enthalten. Cloudkonten, API-Kosten und optionale lokale Whisper-Server samt Modellen sind separat. Mac-Daten liegen unter `~/Library/Application Support/LingerLens`. Mac-Updates erfolgen über ein neues DMG; der integrierte Installer-Updater gilt nur für Windows.

Für den Browsermodus brauchst du Python 3.11+, Node.js 22.12+, FFmpeg/ffprobe und Chrome oder Edge:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

Öffne `http://127.0.0.1:8765/`. `-CheckOnly` prüft nur. Ohne `-Prototype` wird ein vorhandener Desktop-Build geöffnet. Stelle den Companion nicht im LAN oder über einen Reverse Proxy bereit.

## Anbieter und Wiedergabe

Unter Verbindungen und Schlüssel Modell-ID, Endpunkt und Authentifizierung eintragen. Erkennung und Übersetzung sind getrennt; Qwen LiveTranslate und Soniox können beides in einer Sitzung liefern. Übersetzungsprotokolle: OpenAI-kompatibel, Qwen-MT, Anthropic Messages, Google Gemini. Erkennung: DashScope, Soniox, Deepgram, OpenAI, AssemblyAI, Volcano Engine, ElevenLabs, Speechmatics, Tencent. Diese Protokollliste ist keine Garantie für jedes Konto, Modell oder jede Sprache.

Die Protokollauswahl zeigt kurze Anbieternamen. Nach der ASR-Auswahl trägt die Modellauswahl eine unterstützte Modell-ID ein; eigene Gateway-IDs bleiben manuell editierbar. Bei Tencent wird auch die Engine aktualisiert. Neue Soniox-Profile benötigen keinen separaten Übersetzer. Aktivieren Sie den Übersetzungsfallback erst nach dessen Einrichtung. Die Sieben-Sekunden-Grenze veröffentlicht bestätigten Text; sie erzwingt keine Anbieterantwort und bestätigt keine veränderliche Hypothese. Zeit- und Übersetzungsgrenzen stehen im [ASR-Kompatibilitätsaudit](../ASR-COMPATIBILITY.md).

YouTube-, Bilibili- oder Twitch-HTTPS-URL einfügen, H.264/AVC und AAC bevorzugen und starten. Zielverzögerung: 11–60 Sekunden, Standard 15; die tatsächliche Latenz hängt zusätzlich von Stream und Netzwerk ab. Die Player-Vollbildtaste nimmt Untertitel mit. Öffentliche Streams zuerst ohne Cookies testen. Der Import akzeptiert unterstützte Header, Tabellen oder Netscape-Exporte; Bilibili-Anmeldung benötigt `SESSDATA`. Cookies umgehen weder DRM noch Bezahl-, Regions- oder Bot-Schutz.

## Verzögerungswerte verstehen

**Lokaler Segmentabstand** misst den Abstand vom aktuellen Bild zum neuesten vollständigen lokalen Videosegment. Plattformlatenz und unvollständige Segmente sind nicht enthalten; fehlende Messwerte erscheinen als **—**. Die **Zeitreserve bei Übersetzungseingang** ist bei früher Ankunft positiv, bei Verspätung negativ. Es zählen nur aktuelle Beobachtungen bei normaler Wiedergabe im Vordergrund. Pause, Springen, beschleunigtes Aufholen und veraltete Daten machen bisherige Empfehlungen ungültig. **Zielverzögerung auf 19 Sekunden setzen** setzt das gesamte Ziel auf 19 Sekunden. Die Empfehlung beruht auf jüngsten Ergebnissen und garantiert keine rechtzeitige Ankunft jedes weiteren Untertitels.

## Datenschutz und Fehlerbehebung

Cloud-ASR erhält Audio, Übersetzung Text und Kontext. Desktopdaten liegen meist unter `%APPDATA%/LingerLens`; gespeicherte Schlüssel sind in den Einstellungen sichtbar. Nicht während Bildschirmfreigabe öffnen und keine Laufzeitdaten, signierten URLs oder ungeprüften Logs veröffentlichen. Der Updater prüft Größe und SHA-256, aber keine Herausgebersignatur.

Fehlende EXE: `-Prototype` verwenden. Fehlendes Modul: mit demselben Python installieren. Belegter Port: Companion schließen oder anderen Port verwenden. 403/keine Formate: URL, Konto, Region und Cookies prüfen. Fehlende Untertitel: ASR-, Übersetzungs- und Sprachprofil prüfen.

## Entwicklung

```powershell
npm ci --ignore-scripts
npm run ci
npm run desktop:test
git diff --check
```

Die lokalen Fixtures bestätigen keine echte Wiedergabe, kein Konto, keine Abrechnung und keine Übersetzungsqualität. Siehe [Entwicklung](../DEVELOPMENT.md), [Mitwirken](../../CONTRIBUTING.md) und [Release](../RELEASING.md).
