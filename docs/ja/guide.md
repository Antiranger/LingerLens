# LingerLens 利用ガイド

[文書一覧](../README.md) · 対象 0.1.0 · 更新日 2026-09-29

## 起動

Windows の既定のインストール先は `%LOCALAPPDATA%/Programs/lingerlens`、ユーザーデータは別の `%APPDATA%/LingerLens` に保存されます。0.1.1 以降はウィザードでインストール先を選択できます。

初回起動時には設定済みモデル、API キー、インポート済み Cookie はありません。モデル設定で自分の接続を追加してください。更新や再インストールではこのコンピューターの既存データが保持されるため、開発環境で以前の設定が表示されても配布物に含まれるとは限りません。録画には新しい専用データディレクトリを使い、ユーザーデータやバックアップを配布物に含めないでください。

ビルド対象は Windows x64 と macOS arm64/x64 です。[Releases](https://github.com/Antiranger/LingerLens/releases) の公開済みファイルから選んでください。Windows は EXE、Mac はチップに合う DMG を開き、アプリケーションにドラッグします。プレビュー版は未署名、Mac 版は未公証です。`SHA256SUMS.txt` で確認してください。

Electron、Python、FFmpeg/ffprobe、yt-dlp、フォント、日本語辞書を同梱しています。クラウドのアカウント・API 料金と任意のローカル Whisper サーバー・モデルは別途必要です。Mac のデータは `~/Library/Application Support/LingerLens` に保存されます。Mac の更新は新しい DMG から行います。アプリ内インストーラー更新は Windows 専用です。

ブラウザー開発には Python 3.11 以上、Node.js 22.12 以上、FFmpeg/ffprobe、Chrome または Edge が必要です。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Python .\.venv\Scripts\python.exe
.\start-lingerlens.cmd -Prototype -Python .\.venv\Scripts\python.exe
```

`http://127.0.0.1:8765/` を開きます。`-CheckOnly` は確認だけです。`-Prototype` なしは既存のデスクトップ EXE を開きます。Companion を LAN やリバースプロキシに公開しないでください。

## 設定と再生

接続先とキーで正確なモデル ID、エンドポイント、認証を登録します。認識と翻訳は別設定が基本で、Qwen LiveTranslate や Soniox の内蔵翻訳も利用できます。翻訳は OpenAI 互換、Qwen-MT、Anthropic Messages、Google Gemini、認識は DashScope、Soniox、Deepgram、OpenAI、AssemblyAI、Volcano Engine、ElevenLabs、Speechmatics、Tencent のプロトコルに対応します。これは実アカウントで全モデルを確認したという意味ではありません。

プロトコルは短いサービス名で表示されます。ASR を選んだらモデル一覧から対応モデル ID を入力するか、独自サービスの ID を手動で入力してください。Tencent の選択はエンジンも更新します。新しい Soniox 設定では別の翻訳モデルは不要です。翻訳フォールバックは翻訳モデルを設定した後に有効にしてください。7 秒の上限は確定済み字幕の公開期限であり、未確定の仮説やサービスがまだ返していない結果には適用できません。時刻と翻訳の制約は [ASR 互換性監査](../ASR-COMPATIBILITY.md) を参照してください。

YouTube、Bilibili、Twitch の HTTPS URL を入力し、H.264/AVC と AAC を優先して開始します。遅延は 11～60 秒、初期値 15 秒です。実際の遅延は配信元とネットワーク次第です。プレーヤーの全画面ボタンを使うと字幕も全画面になります。公開配信はまず Cookie なしで試し、必要な場合だけ対応形式を取り込みます。Bilibili ログインには `SESSDATA` が必要です。Cookie は DRM、購入制限、地域制限、ボット対策を回避しません。

## 遅延表示の読み方

**ローカル区間の遅延**は、再生中の映像から最新の完成済みローカル動画区間までの差です。配信サービス自体の遅延や未完成区間は含まず、測定できない場合は **—** と表示します。**訳文到着の余裕**は正なら早着、負なら遅着です。前面で通常再生中に届いた最近の訳文だけを使い、一時停止、シーク、追いつき再生、データの期限切れで以前の提案は無効になります。**目標遅延を 19 秒に設定**というボタンは目標全体を 19 秒にします。最近の観測による提案であり、以後すべての字幕が間に合う保証ではありません。

## データと問題解決

クラウド認識には音声、翻訳には字幕と文脈を送信します。デスクトップデータは通常 `%APPDATA%/LingerLens` に保存され、設定画面にはキーが表示されます。画面共有中は開かず、実行時データや署名付き URL、未確認ログを公開しないでください。更新器はサイズと SHA-256 を確認しますが、発行元の署名は確認しません。

EXE がない場合は `-Prototype`、モジュール不足は同じ Python で依存関係をインストール、ポート使用中は対象 Companion を閉じるか別ポートを使用します。403 や画質不足は URL、アカウント、地域、Cookie を確認し、字幕不足は ASR と翻訳の設定を確認します。

## 開発

```powershell
npm ci --ignore-scripts
npm run ci
npm run desktop:test
git diff --check
```

これらはローカル fixture の確認であり、実配信、実アカウント、請求、翻訳品質の保証ではありません。[開発](../DEVELOPMENT.md)、[貢献](../../CONTRIBUTING.md)、[リリース](../RELEASING.md)も参照してください。
