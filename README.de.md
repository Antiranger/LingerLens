<p align="center"><img src="desktop/assets/icon.png" width="96" alt="LingerLens"></p>
<h1 align="center">LingerLens</h1>
<p align="center"><strong>Livestreams verstehen. Mit passenden Untertiteln.</strong></p>

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

Livestreams mit etwas Zeit für die Untertitel: LingerLens spielt **YouTube Live, Bilibili Live und Twitch** lokal verzögert ab und ergänzt Spracherkennung, übersetzte Untertitel und Livechat.

**Vorschau für Windows und macOS · Anwendungscode unter MIT · Eigene Zugangsdaten erforderlich.** Verfügbarkeit und Sprachunterstützung hängen von Stream, Konto und Dienst ab.

![Oberfläche beim ersten Start ohne Zugangsdaten](docs/assets/player.png)

## Download

[**GitHub Releases →**](https://github.com/Antiranger/LingerLens/releases)

Windows x64: `.exe`. Mac: `macos-arm64.dmg` für Apple Silicon oder `macos-x64.dmg` für Intel. DMG öffnen und LingerLens in Programme ziehen. Verfügbar sind die Anhänge eines veröffentlichten Releases. Vorschaupakete sind unsigniert; macOS-Pakete sind nicht notarisiert. Prüfe den Download mit `SHA256SUMS.txt`.

## Warum die Verzögerung?

Erkennung und Übersetzung brauchen Zeit. LingerLens verzögert das Bild kurz, damit Sprache und übersetzte Untertitel gemeinsam ankommen. Beginne mit 15 Sekunden und passe das Ziel an Anbieter und Netzwerk an.

## Funktionen

- Lokale HLS-Wiedergabe mit einer Zielverzögerung von 11–60 Sekunden, standardmäßig 15 Sekunden. Plattform und Netzwerk beeinflussen die tatsächliche Verzögerung.
- Originaltext und Übersetzung als Untertitel, auch bei überlappenden Wortbeiträgen, sofern der Dienst Sprecherinformationen liefert.
- Konfigurierbare Erkennungs-, Übersetzungs- und Ersatzprofile, optionale Chatübersetzung sowie Nutzungs- und Kostenschätzungen bei ausreichenden Daten.
- Verschiebbares Untertitelfenster mit gespeicherter Darstellung, Vollbild für den gesamten Player, Diagnoseprotokolle und Updateprüfung.
- Oberfläche auf Chinesisch (vereinfacht), Englisch, Japanisch, Deutsch und Russisch. Die Oberflächensprache ist unabhängig von den Untertitelsprachen.

## Einstieg

Installiere das Paket für dein System und öffne **LingerLens**. Electron, Python, FFmpeg/ffprobe, yt-dlp, Schriftarten und das japanische Wörterbuch sind enthalten; Entwicklungswerkzeuge werden nicht benötigt. Cloud-Dienste benötigen Internet, eigene API-Schlüssel und gegebenenfalls kostenpflichtige Konten. Ein lokaler Whisper-Server samt Modell ist nicht enthalten.

Weitere Hilfe: [deutsche Anleitung](docs/de/guide.md) und [Desktop-Builds für Windows und macOS](desktop/README.md).

1. Öffne die Einstellungen für Verbindungen und Schlüssel und richte Erkennung und Übersetzung ein.
2. Wähle die gesprochene Sprache und die Zielsprache; aktiviere bei Bedarf Untertitel.
3. Füge die Livestream-URL ein, ermittle Qualitätsstufen und starte ein kompatibles Format.
4. Importiere Cookies nur, wenn eine Anmeldung nötig ist. Stoppe die Wiedergabe vor einem Kontowechsel oder Update.

Die [deutsche Anleitung](docs/de/guide.md) behandelt Einrichtung, Dienste, Cookies, Datenschutz, Fehlerbehebung und Entwicklung.

## Aus dem Quellcode starten

Benötigt werden Windows, Python **3.11+**, Node.js **22.12+**, FFmpeg/ffprobe im PATH sowie Chrome oder Edge. Solange das Repository privat ist, brauchst du GitHub-Zugriffsrechte.

```powershell
git clone https://github.com/Antiranger/LingerLens.git
cd LingerLens
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

Öffne <http://127.0.0.1:8765/>. Bootstrap prüft Werkzeuge und die yt-dlp-Prüfsumme und installiert Abhängigkeiten. Mit `-CheckOnly` wird nur geprüft. Ein Desktop-Build entsteht dabei nicht.

Ohne `-Prototype` startet der Befehl eine vorhandene `release/win-unpacked/LingerLens.exe`. Die [Desktop-Bauanleitung](desktop/README.md) beschreibt deren Erstellung.

## Datenschutz und Grenzen

Cloud-Spracherkennung erhält Audio; die Übersetzung erhält Text und Kontext. Bei integrierter Erkennung und Übersetzung gehen Audio und Übersetzungsanweisungen an denselben Dienst. Ein lokaler Whisper-kompatibler Server muss separat eingerichtet werden. Lokale Erkennung macht eine Cloud-Übersetzung nicht lokal.

Schlüssel und Cookies liegen in lokalen Dateien, nicht in einem verschlüsselten Tresor. **Die Einstellungen zeigen gespeicherte Schlüssel an.** Öffne sie nicht während einer Bildschirmfreigabe und lade keine Laufzeitdaten oder ungeprüften Protokolle in Issues hoch. Desktop-Daten liegen üblicherweise unter `%APPDATA%/LingerLens` und bleiben bei der Deinstallation standardmäßig erhalten.

LingerLens umgeht weder DRM noch Bezahlschranken, Kontobeschränkungen oder Bot-Schutz. Gebühren, Änderungen an Streamingdiensten und Übersetzungsqualität erfordern eigene Prüfung. Offline-Tests bestätigen keine Kompatibilität mit jedem echten Konto.

## Entwicklung und Mitarbeit

Führe vor einem Beitrag `npm run ci` und `git diff --check` aus. CI prüft Dokumentation, Lizenzen, Syntax und lokale Tests. Optionale Browser- und Streamlink-Tests können ohne ihre Abhängigkeiten übersprungen werden; siehe [Entwicklung](docs/DEVELOPMENT.md).

[Dokumentation](docs/README.md) · [Mitwirken](CONTRIBUTING.md) · [Verhaltenskodex](CODE_OF_CONDUCT.md) · [Hilfe](SUPPORT.md) · [Sicherheit](SECURITY.md) · [Änderungen](CHANGELOG.md) · [Releaseablauf](docs/RELEASING.md)

Der Anwendungscode steht unter der [MIT-Lizenz](LICENSE). Mitgelieferte Komponenten behalten ihre eigenen Lizenzen. Beachte vor einer Weitergabe die [Drittanbieterhinweise](THIRD_PARTY_NOTICES.md) und die Pflichten zur Bereitstellung des zugehörigen Quellcodes.
