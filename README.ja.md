# LingerLens

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

字幕が追いつく時間をつくるライブプレーヤーです。**YouTube Live、Bilibili Live、Twitch** の映像をローカルで遅延再生し、音声認識、翻訳字幕、ライブチャットを組み合わせます。

**Windows x64 向けプレビュー版 · アプリのコードは MIT ライセンス · API の認証情報は各自で用意。** 現在のバージョンは 0.1.0 です。macOS、Linux、Android 向けの対応パッケージはありません。再生可否や認識・翻訳言語は、配信元、アカウント、選択したサービスに依存します。

## 主な機能

- ローカル HLS の遅延再生。目標は初期値 15 秒、設定範囲は 11～60 秒です。実際の遅延には配信元とネットワークも影響します。
- 原文と訳文の字幕。認識サービスが話者情報を返す場合、重なった発話も表示できます。
- 認識・翻訳・代替モデルの設定、任意のチャット翻訳、必要な情報が得られる場合の利用量・料金見積もり。
- 位置とスタイルを保存できる字幕ウィンドウ、プレーヤー全体の全画面表示、診断記録、更新確認。
- 簡体字中国語、英語、日本語、ドイツ語、ロシア語の画面表示。画面と字幕の言語は別々に設定します。

## 使い始める

メンテナーから提供された Windows x64 インストーラーを実行し、**LingerLens** を開きます。Electron、Python、FFmpeg/ffprobe、yt-dlp、日本語辞書を同梱しているため、開発ツールは不要です。プレビュー版は未署名の場合があります。実行前に入手元とチェックサムを確認してください。

配布先は [LingerLens Releases](https://github.com/Antiranger/LingerLens-releases/releases) です。このリンクは検証済みの公開版の存在を保証しません。適切な版がなければ、下記のソース実行手順を使ってください。

1. 接続先と API キーの設定を開き、認識・翻訳サービスを登録します。
2. 配信の言語と翻訳先を選び、必要に応じて字幕を有効にします。
3. 配信 URL を貼り付け、画質を取得して、対応形式で再生します。
4. ログインが必要な場合だけ Cookie を取り込みます。アカウント変更や更新の前には再生を停止してください。

[日本語ガイド](docs/ja/guide.md)に、設定、Cookie、プライバシー、問題の切り分け、開発手順をまとめています。

## ソースから実行する

Windows、Python **3.11 以上**、Node.js **22.12 以上**、PATH 上の FFmpeg/ffprobe、Chrome または Edge が必要です。リポジトリが非公開の間は GitHub のアクセス権も必要です。

```powershell
git clone https://github.com/Antiranger/LingerLens.git
cd LingerLens
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

<http://127.0.0.1:8765/> を開きます。Bootstrap はツールと yt-dlp のチェックサムを確認して依存関係をインストールします。`-CheckOnly` なら確認のみです。デスクトップ版のビルドは行いません。

`-Prototype` を付けない起動コマンドは、既存の `release/win-unpacked/LingerLens.exe` を開きます。[ビルド手順](desktop/README.md)も参照してください。

## プライバシーと制限

クラウド認識には音声を、翻訳には文章と文脈を送信します。認識と翻訳を一体で行うサービスでは、同じ接続先が音声と翻訳指示を受け取ります。ローカルの Whisper 互換サービスは別途導入が必要で、ローカル認識を使ってもクラウド翻訳への送信は続きます。

キーと Cookie は通常のローカルファイルに保存され、暗号化された保管庫ではありません。**設定画面には保存済みのキーが表示されます。** 画面共有中は開かず、実行時データや未確認のログを Issue に添付しないでください。データは通常 `%APPDATA%/LingerLens` にあり、既定ではアンインストール後も残ります。

DRM、有料アクセス、アカウント制限、ボット対策を回避する機能はありません。サービス料金、サイトの変更、翻訳精度には個別の確認が必要です。オフラインテストはすべての実アカウントでの動作を保証しません。

## 開発と参加

変更前後に `npm run ci` と `git diff --check` を実行します。CI は文書、ライセンス、構文、ローカルテストを確認します。任意のブラウザー・Streamlink テストは依存関係がなければスキップされます。[開発文書](docs/DEVELOPMENT.md)を参照してください。

[文書一覧](docs/README.md) · [貢献方法](CONTRIBUTING.md) · [行動規範](CODE_OF_CONDUCT.md) · [サポート](SUPPORT.md) · [セキュリティ](SECURITY.md) · [変更履歴](CHANGELOG.md) · [リリース手順](docs/RELEASING.md)

アプリのコードは [MIT ライセンス](LICENSE) です。同梱ソフトウェアにはそれぞれのライセンスが適用されます。再配布の前に[第三者ソフトウェアの表示](THIRD_PARTY_NOTICES.md)と対応ソースの提供要件を確認してください。
