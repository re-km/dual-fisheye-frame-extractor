# Dual Fisheye Frame Extractor

DJI Osmo 360（`.osv`）/ Insta360（`.insv`）などのデュアル魚眼動画から、Agisoft Metashape用のフレーム画像を抽出するCLIツールです。

## 特徴

- デュアル魚眼動画の **front / back 2ストリーム** を個別に抽出
- **対話モード**: FPS設定・回転方向確認をステップバイステップで実行
- **回転補正**: front / back で独立した回転角度を指定可能（テストフレームによる目視確認）
- **Metashape命名規則** に準拠したファイル名で出力（`front_NNNN_M.jpg` / `back_NNNN_M.jpg`）
- **ドラッグ＆ドロップ対応** のバッチファイル付き

## 必要環境

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/)（`ffmpeg` / `ffprobe` にパスが通っていること）
- Windows（バッチファイル・エクスプローラー連携）

## ファイル構成

```
extract_dual_fisheye.py   # メインスクリプト
extract_frames.bat        # ドラッグ＆ドロップ用バッチファイル
frames/                   # デフォルト出力ディレクトリ
```

## 使い方

### バッチファイル（推奨）

`.osv` / `.insv` ファイルを `extract_frames.bat` にドラッグ＆ドロップするだけで実行できます。

### コマンドライン

```bash
# 対話モード（FPS・回転方向を対話的に設定）
python extract_dual_fisheye.py input.osv

# FPS指定
python extract_dual_fisheye.py input.osv --fps 1

# 出力先・品質を指定
python extract_dual_fisheye.py input.osv --fps 2 -o ./output --quality 90

# 回転角度を直接指定（対話スキップ）
python extract_dual_fisheye.py input.osv --fps 1 --rotate-front 90 --rotate-back 270
```

### オプション一覧

| オプション | 説明 | デフォルト |
|---|---|---|
| `input` | 入力ファイルパス（`.osv` / `.insv`） | 必須 |
| `-o`, `--output` | 出力ディレクトリ | `./frames` |
| `--fps` | 抽出FPS（省略時は対話的に設定） | - |
| `--quality` | JPEG品質（1-100） | 95 |
| `--rotate-front` | front回転角度（0/90/180/270） | 対話で確認 |
| `--rotate-back` | back回転角度（0/90/180/270） | 対話で確認 |

## 処理の流れ

1. **ストリーム情報取得** — ffprobeで動画の2ストリーム（front/back）を検出
2. **FPS設定** — 動画の長さ・予想枚数を表示し、抽出間隔を決定
3. **回転方向確認** — テストフレームを4方向（0°/90°/180°/270°）で抽出し、エクスプローラーで表示。目視で正しい向きを選択（front/back独立）
4. **フレーム抽出** — ffmpegで全フレームを抽出・回転補正を適用
5. **リネーム** — Metashape命名規則（`{prefix}_{連番}_{グローバル連番}.jpg`）に変換

## 出力ファイル名の形式

```
front_0001_0.jpg    # front 1枚目
front_0002_1.jpg    # front 2枚目
...
back_0001_100.jpg   # back 1枚目（グローバル連番は front から継続）
back_0002_101.jpg   # back 2枚目
```

## 回転補正について

DJI Osmo 360 の `.osv` ファイルにはメタデータに回転情報が含まれていないため、初回実行時にテストフレームで正しい向きを確認する必要があります。同じカメラ・設定であれば回転角度は固定なので、2回目以降は `--rotate-front` / `--rotate-back` で直接指定できます。

## License

MIT
