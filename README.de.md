# LingerLens

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

Livestreams mit etwas Zeit für die Untertitel: LingerLens spielt **YouTube Live, Bilibili Live und Twitch** lokal verzögert ab und ergänzt Spracherkennung, übersetzte Untertitel und Livechat.

**Vorschau für Windows x64 · Anwendungscode unter MIT · Eigene Zugangsdaten erforderlich.** Aktuelle Version: 0.1.0. Unterstützte Pakete für macOS, Linux oder Android gibt es nicht. Verfügbarkeit und Sprachunterstützung hängen vom Stream, vom Konto und vom gewählten Dienst ab.

## Funktionen

- Lokale HLS-Wiedergabe mit einer Zielverzögerung von 11–60 Sekunden, standardmäßig 15 Sekunden. Plattform und Netzwerk beeinflussen die tatsächliche Verzögerung.
- Originaltext und Übersetzung als Untertitel, auch bei überlappenden Wortbeiträgen, sofern der Dienst Sprecherinformationen liefert.
- Konfigurierbare Erkennungs-, Übersetzungs- und Ersatzprofile, optionale Chatübersetzung sowie Nutzungs- und Kostenschätzungen bei ausreichenden Daten.
- Verschiebbares Untertitelfenster mit gespeicherter Darstellung, Vollbild für den gesamten Player, Diagnoseprotokolle und Updateprüfung.
- Oberfläche auf Chinesisch (vereinfacht), Englisch, Japanisch, Deutsch und Russisch. Die Oberflächensprache ist unabhängig von den Untertitelsprachen.

## Einstieg

Installiere das vom Maintainer bereitgestellte Windows-x64-Paket und öffne **LingerLens**. Electron, Python, FFmpeg/ffprobe, yt-dlp und das japanische Wörterbuch sind enthalten; Entwicklungswerkzeuge werden nicht benötigt. Vorschaupakete können unsigniert sein. Prüfe Herkunft und Prüfsumme vor dem Ausführen.

Der Downloadkanal ist [LingerLens Releases](https://github.com/Antiranger/LingerLens-releases/releases). Der Link garantiert keine bereits geprüfte öffentliche Version. Fehlt ein passendes Paket, nutze die Anleitung für den Quellcode.

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
