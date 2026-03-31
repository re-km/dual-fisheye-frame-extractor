# Dual Fisheye Frame Extractor

DJI Osmo 360（`.osv`）/ Insta360（`.insv`）などのデュアル魚眼動画から、Agisoft Metashape用のフレーム画像を抽出するツールです。Streamlit GUI とCLIの両方に対応しています。

> **注意**: 本ツールはMetashapeへ入力する魚眼フレームの準備が主目的です。マスク生成は Metashape で SfM → COLMAP export を行った後のパースペクティブ画像に対して実行するため、外部スクリプト（`gen_masks_sam3.py`）と Metashape が別途必要です。

## 特徴

- **Streamlit GUI** でフレーム抽出・露出補正・マスク生成を順に実行
- デュアル魚眼動画の **front / back 2ストリーム** を個別に抽出
- **シャープネス選択**（デフォルトON）: 各ウィンドウから最もブレの少ないフレームを自動選択
- **LUT適用**（デフォルトON）: D-Log M → Rec.709 など `.cube` LUTを自動検出・適用
- **10bit対応**: 16bit PNG / TIFF 出力で10bit D-Log M等の階調を保持
- **複数動画の連番抽出**: 2台同時撮影などで複数ファイルをまとめて通し連番で出力
- **露出補正**: 白飛び・暗すぎフレームをガンマ補正で自動調整（3DGSフローター予防）
- **SAM3マスク生成**（要外部スクリプト）: Metashape COLMAP export後のパースペクティブ画像に対し、front/back を `--file-pattern` で分離してマスクを生成
- **回転補正**: front / back で独立した回転角度を指定可能
- **Metashape命名規則** に準拠したファイル名で出力

## 必要環境

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/)（`ffmpeg` / `ffprobe` にパスが通っていること）
- [Pillow](https://pypi.org/project/Pillow/) / [NumPy](https://pypi.org/project/numpy/)（シャープネス選択に必要）
- [Streamlit](https://streamlit.io/)（GUI使用時）
- Windows

```bash
pip install Pillow numpy streamlit opencv-python
```

## ファイル構成

```
app.py                    # Streamlit GUI（抽出→露出補正→マスク生成）
start_gui.bat             # GUI起動バッチ
extract_dual_fisheye.py   # フレーム抽出（CLI）
adjust_exposure.py        # 露出補正（白飛び・暗すぎのガンマ補正）
extract_frames.bat        # ドラッグ＆ドロップ用バッチファイル（CLI）
*.cube                    # LUTファイル（各自配置、Git追跡対象外）
config.json               # GUI設定（自動生成、Git追跡対象外）
```

## セットアップ

### LUTファイルの配置

D-Log M で撮影した動画を使う場合、DJI公式サイトから「D-Log M to Rec.709」の `.cube` ファイルをダウンロードし、このスクリプトと同じフォルダに配置してください。起動時に自動検出されます。

## 使い方

### GUI（推奨）

`start_gui.bat` をダブルクリック、または：

```bash
streamlit run app.py
```

GUIでは以下のステップを順に実行できます：

1. **動画選択** — パス入力 + ffprobeでストリーム情報を表示
2. **抽出設定** — 出力形式、FPS、回転角度、シャープネス選択、LUT
3. **フレーム抽出** — リアルタイムログ表示付きで実行
3.5. **露出補正**（任意） — 白飛び・暗すぎフレームの検出とガンマ補正
   - dry-run（分析のみ）/ 実行 / 復元 の3モード
   - しきい値・目標輝度をGUIで調整可能
4. **マスク生成**（任意） — Metashape COLMAP export後の `images/` に対してSAM3マスクを生成
   - **前提**: Metashapeで SfM → COLMAP形式 export を完了していること
   - 外部スクリプト `gen_masks_sam3.py`（`3dgs_work_flow/` に配置）を呼び出し
   - front / back / both を選択可能（`--file-pattern` で自動分離）
   - マスクオーバーレイプレビュー付き

サイドバーで作業ディレクトリ・LUT・SAM3環境などの共通設定を保存できます。

### CLI

```bash
# 対話モード（すべての設定を対話的に選択）
python extract_dual_fisheye.py input.osv

# 複数動画を連番で抽出（2台同時撮影等）
python extract_dual_fisheye.py cam_A.osv cam_B.osv

# FPS・出力形式を指定
python extract_dual_fisheye.py input.osv --fps 1 --format png

# LUTなし・シャープネス選択なしで高速抽出
python extract_dual_fisheye.py input.osv --fps 1 --no-lut --no-sharp

# 回転角度を直接指定（対話スキップ）
python extract_dual_fisheye.py input.osv --fps 1 --rotate-front 90 --rotate-back 270
```

### CLIオプション一覧

| オプション | 説明 | デフォルト |
|---|---|---|
| `input` | 入力ファイルパス（複数指定可） | 必須 |
| `-o`, `--output` | 出力ディレクトリ | `./frames` |
| `--fps` | 抽出FPS（省略時は対話的に設定） | - |
| `--format` | 出力形式: `jpg`(8bit) / `png`(16bit) / `tiff`(16bit) | 対話で選択 |
| `--quality` | JPEG品質（1-100、PNG/TIFFでは無視） | 95 |
| `--lut` | `.cube` LUTファイルパス | 自動検出 |
| `--no-lut` | LUT適用をスキップ | - |
| `--no-sharp` | シャープネス選択を無効化（固定間隔で抽出） | - |
| `--rotate-front` | front回転角度（0/90/180/270） | 対話で確認 |
| `--rotate-back` | back回転角度（0/90/180/270） | 対話で確認 |

## ワークフロー

```
デュアル魚眼動画(.osv/.insv)
  → ① フレーム抽出 (extract_dual_fisheye.py)          → frames/
  → ② 露出補正 (adjust_exposure.py)（任意）            → frames/（上書き）
  → ③ Metashape SfM + COLMAP export（外部ツール）      → images/ + sparse/0/
  → ④ SAM3 マスク生成 (gen_masks_sam3.py)（任意）      → masks/
  → ⑤ Lichtfeld Studio トレーニング（外部ツール）
```

本ツールが担当するのは ①②（フレーム画像の準備）です。③以降は Metashape や Lichtfeld Studio など外部ツールが必要です。

マスク生成（④）は Metashape が COLMAP export 時に生成するパースペクティブ補正済みの `images/` に対して実行します。魚眼の `frames/` ではなく、歪み補正済み画像を対象とすることで SAM3 の検出精度を確保しています。

## 処理の流れ（フレーム抽出）

1. **ストリーム情報取得** — ffprobeで動画の2ストリーム（front/back）を検出、ビット深度・pix_fmtを表示
2. **LUT設定** — スクリプトと同じフォルダから `.cube` ファイルを自動検出
3. **出力形式選択** — 10bitソースの場合はPNG/TIFFを推奨
4. **FPS設定** — 動画の長さ・予想枚数を表示し、抽出間隔を決定
5. **回転方向確認** — テストフレームを4方向で抽出し、エクスプローラーで表示。目視で正しい向きを選択
6. **フレーム抽出**（2パス方式）
   - **Pass 1**: ソースFPSで候補フレームを低解像度JPEGで抽出
   - **シャープネス評価**: Laplacian分散で各ウィンドウから最もシャープな候補を選択
   - **Pass 2**: 選択フレームのみ高品質で再抽出（LUT適用・回転補正）
7. **リネーム** — 通し連番付きのMetashape命名規則に変換

## 出力ファイル名の形式

### 単一動画

```
v1_front_0001_0.png     # front 1枚目
v1_front_0002_1.png     # front 2枚目
v1_back_0001_100.png    # back 1枚目（連番は front から継続）
```

### 複数動画（連番）

```
v1_front_0001_0.png     # 動画1 front
v1_back_0001_60.png     # 動画1 back
v2_front_0001_120.png   # 動画2 front（前の動画の続きから）
v2_back_0001_180.png    # 動画2 back
```

## 回転補正について

DJI Osmo 360 の `.osv` ファイルにはメタデータに回転情報が含まれていないため、初回実行時にテストフレームで正しい向きを確認する必要があります。同じカメラ・設定であれば回転角度は固定なので、2回目以降は `--rotate-front` / `--rotate-back` で直接指定できます。

## 10bit D-Log M 対応について

DJI OSMO の 10bit D-Log M で撮影した動画はフラットな色合いで記録されています。本ツールでは：

- **LUT自動適用**: `.cube` ファイルをフォルダに置くだけで D-Log M → Rec.709 変換を適用
- **16bit出力**: `--format png` / `tiff` で10bitの階調情報を保持したまま出力
- **シャープネス選択**: LUT適用前のフラットな状態ではなく、ソースフレームの構造的シャープネスで評価

## License

MIT
