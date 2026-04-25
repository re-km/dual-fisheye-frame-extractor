"""
デュアル魚眼フレーム抽出 — Streamlit GUI

ワークフロー:
  Step 1: 動画選択 — パス入力 + ffprobeでストリーム情報表示
  Step 2: 抽出設定 — 形式, FPS, 回転, シャープネス, LUT
  Step 3: フレーム抽出 — extract_dual_fisheye.py をsubprocessで実行
  Step 4: 露出補正（任意） — adjust_exposure.py
  Step 5: マスク生成（任意） — gen_masks_sam3.py を --file-pattern付きで呼出
  出力サマリー + マスクオーバーレイプレビュー

起動:
  streamlit run app.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "config.json"

DEFAULT_CONFIG = {
    "work_dir": "",
    "lut_path": "",
    "sam3_python": r"C:\Users\kmWin\AppData\Local\Programs\Python\Python311\python.exe",
    "gen_masks_script": str(Path(SCRIPT_DIR).parent / "3dgs_work_flow" / "gen_masks_sam3.py"),
}

SUPPORTED_FORMATS = ("jpg", "png", "tiff")
ROTATION_OPTIONS = [0, 90, 180, 270]


# ===== ユーティリティ =====

def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def run_command(cmd: list[str], log_container) -> int:
    """コマンドを実行し、リアルタイムでログを表示する。"""
    log_lines = []
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    for line in process.stdout:
        log_lines.append(line.rstrip())
        log_container.code("\n".join(log_lines[-100:]), language="text")
    process.wait()
    return process.returncode


def probe_streams(input_path: str) -> list[dict] | None:
    """ffprobeでストリーム情報を取得。失敗時はNone。"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                "-show_entries", "stream=index,codec_name,width,height,r_frame_rate,nb_frames,pix_fmt,tags:stream_side_data",
                "-select_streams", "v",
                str(input_path),
            ],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def get_duration(input_path: str) -> float | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(input_path)],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        dur = data.get("format", {}).get("duration")
        return float(dur) if dur else None
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def get_rotation(stream: dict) -> int:
    tags = stream.get("tags", {})
    rotate = tags.get("rotate")
    if rotate is not None:
        return int(rotate) % 360
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return (-int(sd["rotation"])) % 360
    return 0


def format_stream_info(stream: dict, label: str) -> str:
    w = stream.get("width", "?")
    h = stream.get("height", "?")
    codec = stream.get("codec_name", "?")
    r_fps = stream.get("r_frame_rate", "?")
    nb = stream.get("nb_frames", "?")
    pix_fmt = stream.get("pix_fmt", "?")
    rot = get_rotation(stream)
    rot_str = f", rotation={rot}°" if rot != 0 else ""
    return f"{label}: {w}x{h}, {codec}, {pix_fmt}, fps={r_fps}, frames={nb}{rot_str}"


def file_count(path: Path, pattern: str = "*") -> int:
    if path.exists():
        return len(list(path.glob(pattern)))
    return 0


_TRANSPOSE_FILTERS = {
    0: None,
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}


def extract_rotation_preview(input_path: str, stream_index: int,
                             rotation: int, out_path: Path,
                             timestamp: str = "10") -> bool:
    """指定ストリーム・回転で1フレーム抽出（プレビュー用）"""
    filters = [f"scale=480:-2"]
    t = _TRANSPOSE_FILTERS.get(rotation)
    if t:
        filters.insert(0, t)
    vf = ",".join(filters)
    cmd = [
        "ffmpeg", "-v", "error",
        "-noautorotate",
        "-ss", timestamp,
        "-i", str(input_path),
        "-map", f"0:v:{stream_index}",
        "-vf", vf,
        "-frames:v", "1",
        "-q:v", "5",
        "-update", "1",
        str(out_path),
        "-y",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and out_path.exists()


# ===== Streamlit UI =====

st.set_page_config(page_title="デュアル魚眼フレーム抽出", layout="wide")
st.title("デュアル魚眼フレーム抽出")

# --- サイドバー: 共通設定 ---
with st.sidebar:
    st.header("共通設定")
    cfg = load_config()

    work_dir = st.text_input("作業ディレクトリ", value=cfg.get("work_dir", ""),
                             help="フレーム出力先の親ディレクトリ")
    lut_path = st.text_input("LUTファイル (.cube)", value=cfg.get("lut_path", ""),
                             help="空欄＝スクリプトフォルダから自動検出")
    sam3_python = st.text_input("SAM3 Python パス", value=cfg.get("sam3_python", ""),
                                help="SAM3がインストールされたPython実行ファイル")
    gen_masks_script = st.text_input("gen_masks_sam3.py パス",
                                     value=cfg.get("gen_masks_script", ""),
                                     help="マスク生成スクリプトのパス")

    if st.button("設定を保存"):
        new_cfg = {
            "work_dir": work_dir,
            "lut_path": lut_path,
            "sam3_python": sam3_python,
            "gen_masks_script": gen_masks_script,
        }
        save_config(new_cfg)
        st.success("保存しました")

# --- Step 1: 動画選択 ---
st.header("Step 1: 動画選択")

video_paths_text = st.text_area(
    "動画ファイルパス（1行1ファイル、複数対応）",
    height=80,
    help=".osv / .insv ファイルパスを入力",
)

def _clean_path(p: str) -> str:
    p = p.strip().strip('\u200b\u200e\u200f\ufeff\u00a0')
    p = p.strip()
    if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'"):
        p = p[1:-1].strip()
    return p


video_paths = [_clean_path(p) for p in video_paths_text.strip().splitlines() if p.strip()]

if video_paths:
    first_video = video_paths[0]
    if Path(first_video).exists():
        streams = probe_streams(first_video)
        if streams and len(streams) >= 2:
            st.success(f"ストリーム検出: {len(streams)} 個")
            col1, col2 = st.columns(2)
            with col1:
                st.code(format_stream_info(streams[0], "フロント (0:v:0)"))
            with col2:
                st.code(format_stream_info(streams[1], "バック  (0:v:1)"))

            duration = get_duration(first_video)
            if duration:
                st.info(f"動画の長さ: {int(duration // 60):02d}:{int(duration % 60):02d} ({duration:.1f}秒)")

            # ビット深度検出
            pix_fmt = streams[0].get("pix_fmt", "")
            is_high_bit = "10" in pix_fmt or "12" in pix_fmt or "p10" in pix_fmt
        else:
            st.error("ビデオストリームが2個未満です")
            streams = None
            is_high_bit = False
    else:
        resolved = Path(first_video).resolve()
        byte_repr = first_video.encode("unicode_escape").decode("ascii")
        parent = Path(first_video).parent
        parent_exists = parent.exists()
        siblings_hint = ""
        if parent_exists:
            try:
                matches = [p.name for p in parent.iterdir()
                           if p.name.lower().startswith(Path(first_video).stem.lower()[:6])]
                if matches:
                    siblings_hint = "\n  類似ファイル: " + ", ".join(matches[:5])
            except OSError:
                pass
        st.error(
            f"ファイルが見つかりません:\n"
            f"  入力文字列: `{first_video}`\n"
            f"  エスケープ表示: `{byte_repr}`\n"
            f"  解決後: `{resolved}`\n"
            f"  親フォルダ存在: {parent_exists}" + siblings_hint
        )
        streams = None
        is_high_bit = False
else:
    streams = None
    is_high_bit = False

# --- Step 2: 抽出設定 ---
st.header("Step 2: 抽出設定")

col_fmt, col_fps, col_sharp = st.columns(3)

with col_fmt:
    default_fmt_idx = 1 if is_high_bit else 0
    fmt = st.selectbox("出力形式", SUPPORTED_FORMATS, index=default_fmt_idx,
                       help="10bit以上のソースにはPNG/TIFFを推奨")
    if fmt == "jpg":
        quality = st.slider("JPEG品質", 1, 100, 95)
    else:
        quality = 95

with col_fps:
    fps = st.number_input("抽出FPS", min_value=0.1, max_value=30.0,
                          value=1.0, step=0.5)
    if streams and video_paths:
        duration = get_duration(video_paths[0])
        if duration:
            est = int(duration * fps)
            st.caption(f"予想: 約 {est} 枚/レンズ（合計 約 {est * 2} 枚）")

with col_sharp:
    use_sharp = st.checkbox("シャープネス選択", value=True,
                            help="ウィンドウ内で最もシャープなフレームを選択")

st.subheader("回転設定（動画ごと）")
video_rotations: list[tuple[int, int]] = []
if video_paths:
    for vi, vp in enumerate(video_paths):
        st.caption(f"動画 {vi + 1}: `{Path(vp).name}`")
        col_rot_f, col_rot_b = st.columns(2)
        with col_rot_f:
            rf = st.selectbox("フロント 回転", ROTATION_OPTIONS, index=0,
                              key=f"rot_front_{vi}")
        with col_rot_b:
            rb = st.selectbox("バック 回転", ROTATION_OPTIONS, index=0,
                              key=f"rot_back_{vi}")
        video_rotations.append((rf, rb))
else:
    st.caption("動画を入力すると回転選択が表示されます。")

with st.expander("回転プレビュー（動画10秒地点から4方向を抽出）"):
    if not (video_paths and streams and work_dir):
        st.caption("動画選択と作業ディレクトリ設定後に利用できます。")
    else:
        col_btn, col_ts = st.columns([2, 1])
        with col_ts:
            preview_ts = st.text_input("抽出時刻 [秒]", value="10",
                                       help="動画開始からの秒数。暗い/白飛びの場合は変更")
        with col_btn:
            if st.button("回転プレビューを生成"):
                preview_dir = Path(work_dir) / "_rotation_preview"
                preview_dir.mkdir(parents=True, exist_ok=True)
                previews = {}
                with st.spinner("テストフレームを抽出中..."):
                    for vi, vp in enumerate(video_paths):
                        per_video = {"front": {}, "back": {}}
                        for label, sidx in (("front", 0), ("back", 1)):
                            for rot in ROTATION_OPTIONS:
                                out_path = preview_dir / f"v{vi}_{label}_{rot}deg.jpg"
                                ok = extract_rotation_preview(
                                    vp, sidx, rot, out_path, preview_ts
                                )
                                if ok:
                                    per_video[label][rot] = str(out_path)
                        previews[vi] = {"path": vp, "frames": per_video}
                st.session_state["rotation_preview"] = previews

        previews = st.session_state.get("rotation_preview")
        label_ja = {"front": "フロント", "back": "バック"}
        if previews:
            for vi in sorted(previews.keys()):
                entry = previews[vi]
                st.markdown(f"#### 動画 {vi + 1}: `{Path(entry['path']).name}`")
                for label in ("front", "back"):
                    frames = entry["frames"].get(label, {})
                    if not frames:
                        continue
                    st.markdown(f"**{label_ja[label]}**")
                    cols = st.columns(4)
                    for i, rot in enumerate(ROTATION_OPTIONS):
                        p = frames.get(rot)
                        with cols[i]:
                            if p and Path(p).exists():
                                st.image(p, caption=f"{rot}°", use_container_width=True)
                            else:
                                st.caption(f"{rot}° — 失敗")

lut_option = st.radio("LUT", ["自動検出", "指定パス", "なし"], horizontal=True)

# --- Step 3: フレーム抽出 ---
st.header("Step 3: フレーム抽出")

output_dir = Path(work_dir) / "frames" if work_dir else None

if output_dir:
    existing = file_count(output_dir)
    if existing > 0:
        st.warning(f"出力先に既存ファイルあり: {output_dir} ({existing} ファイル)")

if st.button("フレーム抽出を実行", type="primary",
             disabled=not (video_paths and streams and work_dir)):
    extract_script = str(SCRIPT_DIR / "extract_dual_fisheye.py")

    cmd = [sys.executable, extract_script]
    for vp in video_paths:
        cmd.append(vp)
    cmd.extend(["-o", str(Path(work_dir) / "frames")])
    cmd.extend(["--fps", str(fps)])
    cmd.extend(["--format", fmt])
    if fmt == "jpg":
        cmd.extend(["--quality", str(quality)])
    if video_rotations:
        cmd.append("--rotate-front")
        cmd.extend(str(rf) for rf, _ in video_rotations)
        cmd.append("--rotate-back")
        cmd.extend(str(rb) for _, rb in video_rotations)
    if not use_sharp:
        cmd.append("--no-sharp")

    # LUT
    if lut_option == "なし":
        cmd.append("--no-lut")
    elif lut_option == "指定パス" and lut_path:
        cmd.extend(["--lut", lut_path])
    # "自動検出" はデフォルト動作

    st.info(f"実行コマンド:\n```\n{' '.join(cmd)}\n```")
    log_area = st.empty()
    rc = run_command(cmd, log_area)
    if rc == 0:
        n_frames = file_count(Path(work_dir) / "frames")
        st.success(f"フレーム抽出完了: {n_frames} ファイル")
    else:
        st.error(f"エラー（終了コード: {rc}）")


# --- Step 4: 露出補正（任意） ---
st.header("Step 4: 露出補正（任意）")

st.caption("白飛びフレームの輝度を下げ、3DGSフローター発生を予防する")

enable_exposure = st.checkbox("露出補正を有効にする", value=False,
                               help="抽出済みフレームの白飛び・暗すぎを検出し、ガンマ補正で調整")

if enable_exposure:
    exp_frames_dir = Path(work_dir) / "frames" if work_dir else None

    col_exp1, col_exp2 = st.columns(2)
    with col_exp1:
        st.subheader("白飛び検出")
        bright_mean = st.number_input(
            "平均輝度しきい値", 100.0, 255.0, 180.0, key="exp_bright_mean",
            help="平均輝度がこれ以上で白飛び候補")
        bright_overexposed = st.number_input(
            "白飛び画素率しきい値", 0.0, 1.0, 0.10, step=0.01,
            key="exp_bright_clip",
            help="画素の白飛び率がこれ以上で白飛び候補")
        bright_target = st.number_input(
            "補正目標輝度", 80.0, 200.0, 130.0, key="exp_bright_target",
            help="白飛びフレームをこの輝度に補正")

    with col_exp2:
        st.subheader("暗すぎ検出")
        dark_threshold = st.number_input(
            "暗部しきい値", 10.0, 150.0, 60.0, key="exp_dark_thresh",
            help="平均輝度がこれ以下で暗すぎ")
        dark_target = st.number_input(
            "補正目標輝度", 50.0, 200.0, 100.0, key="exp_dark_target",
            help="暗フレームをこの輝度に補正")

    col_exp_btn1, col_exp_btn2, col_exp_btn3 = st.columns(3)

    with col_exp_btn1:
        if st.button("分析のみ (dry-run)", key="exp_dryrun",
                      disabled=not (exp_frames_dir and exp_frames_dir.exists())):
            cmd = [
                sys.executable, str(SCRIPT_DIR / "adjust_exposure.py"),
                "--frames-dir", str(exp_frames_dir),
                "--bright-mean", str(bright_mean),
                "--bright-overexposed-min", str(bright_overexposed),
                "--dark-threshold", str(dark_threshold),
                "--bright-target-mean", str(bright_target),
                "--dark-target-mean", str(dark_target),
                "--dry-run",
            ]
            st.info(f"実行コマンド:\n```\n{' '.join(cmd)}\n```")
            log_area = st.empty()
            rc = run_command(cmd, log_area)
            if rc == 0:
                st.success("分析完了（画像は変更されていません）")
            else:
                st.error(f"エラー（終了コード: {rc}）")

    with col_exp_btn2:
        if st.button("露出補正を実行", type="primary", key="exp_run",
                      disabled=not (exp_frames_dir and exp_frames_dir.exists())):
            cmd = [
                sys.executable, str(SCRIPT_DIR / "adjust_exposure.py"),
                "--frames-dir", str(exp_frames_dir),
                "--bright-mean", str(bright_mean),
                "--bright-overexposed-min", str(bright_overexposed),
                "--dark-threshold", str(dark_threshold),
                "--bright-target-mean", str(bright_target),
                "--dark-target-mean", str(dark_target),
            ]
            st.info(f"実行コマンド:\n```\n{' '.join(cmd)}\n```")
            log_area = st.empty()
            rc = run_command(cmd, log_area)
            if rc == 0:
                st.success("露出補正完了")
            else:
                st.error(f"エラー（終了コード: {rc}）")

    with col_exp_btn3:
        if st.button("元画像に復元", key="exp_restore",
                      disabled=not (exp_frames_dir and exp_frames_dir.exists())):
            cmd = [
                sys.executable, str(SCRIPT_DIR / "adjust_exposure.py"),
                "--frames-dir", str(exp_frames_dir),
                "--restore",
            ]
            log_area = st.empty()
            rc = run_command(cmd, log_area)
            if rc == 0:
                st.success("復元完了")
            else:
                st.error(f"エラー（終了コード: {rc}）")

# --- Step 5: マスク生成（任意） ---
st.header("Step 5: マスク生成（任意）")

st.caption("SAM 3 による動的オブジェクト除去マスク — Metashape/COLMAP export後の images/ に適用")

mask_base_dir = st.text_input(
    "マスク対象ディレクトリ（COLMAP exportのルート）",
    value=work_dir,
    help="images/ と masks/ を含む親ディレクトリ。Metashape→COLMAP export後のパスを指定",
)

mask_target = st.radio("対象", ["both (front + back)", "front のみ", "back のみ"],
                       horizontal=True)

col_m1, col_m2 = st.columns(2)
with col_m1:
    mask_prompts = st.text_input(
        "プロンプト（カンマ区切り）",
        value="human,hand,hair,foot,face,head,backpack,shoulder strap,chest strap,bag,car,coat,jacket,pole,stick,monopod",
        help="除去対象をテキストで指定",
    )
    mask_backend = st.selectbox("バックエンド", ["image", "video"], index=0)

with col_m2:
    closing_iter = st.number_input("クロージング反復", min_value=0, max_value=20,
                                   value=5)
    dilation_iter = st.number_input("膨張反復", min_value=0, max_value=20, value=3)
    fill_holes = st.checkbox("穴埋め", value=True)

images_dir = Path(mask_base_dir) / "images" if mask_base_dir else None
masks_dir = Path(mask_base_dir) / "masks" if mask_base_dir else None

if images_dir and images_dir.exists():
    n_front = file_count(images_dir, "v*_front_*")
    n_back = file_count(images_dir, "v*_back_*")
    n_total = file_count(images_dir)
    st.info(f"images/ — 合計: {n_total}, front: {n_front}, back: {n_back}")
elif mask_base_dir:
    st.warning(f"images/ が見つかりません: {images_dir}")

can_run_mask = (
    mask_base_dir
    and sam3_python
    and gen_masks_script
    and images_dir
    and images_dir.exists()
)

if st.button("マスク生成を実行", type="primary", disabled=not can_run_mask):
    # パターンと実行回数を決定
    runs = []
    if mask_target == "front のみ":
        runs.append(("front", "v*_front_*.*"))
    elif mask_target == "back のみ":
        runs.append(("back", "v*_back_*.*"))
    else:
        runs.append(("front", "v*_front_*.*"))
        runs.append(("back", "v*_back_*.*"))

    all_ok = True
    for label, pattern in runs:
        st.subheader(f"マスク生成: {label}")

        cmd = [
            sam3_python, gen_masks_script,
            "--base-dir", mask_base_dir,
            "--input-dir", "images",
            "--backend", mask_backend,
            "--prompts", mask_prompts,
            "--closing-iterations", str(closing_iter),
            "--dilation-iterations", str(dilation_iter),
            "--file-pattern", pattern,
        ]
        if not fill_holes:
            cmd.append("--no-fill-holes")

        st.info(f"実行コマンド:\n```\n{' '.join(cmd)}\n```")
        log_area = st.empty()
        rc = run_command(cmd, log_area)
        if rc != 0:
            st.error(f"{label} マスク生成失敗（終了コード: {rc}）")
            all_ok = False

    if all_ok:
        n_masks = file_count(masks_dir) if masks_dir else 0
        st.success(f"マスク生成完了: {n_masks} ファイル")


# --- 出力サマリー + プレビュー ---
st.header("出力サマリー")

if work_dir:
    frames_p = Path(work_dir) / "frames"
    # マスクはCOLMAP exportディレクトリ側を参照
    mask_base_p = Path(mask_base_dir) if mask_base_dir else Path(work_dir)
    images_p = mask_base_p / "images"
    masks_p = mask_base_p / "masks"

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        if frames_p.exists():
            n = file_count(frames_p)
            st.metric("フレーム数（魚眼）", n)
        else:
            st.metric("フレーム数（魚眼）", 0)
    with col_s2:
        if images_p.exists():
            n = file_count(images_p)
            st.metric("画像数（パースペクティブ）", n)
        else:
            st.metric("画像数（パースペクティブ）", 0)
    with col_s3:
        if masks_p.exists():
            n = file_count(masks_p, "*.png")
            st.metric("マスク数", n)
        else:
            st.metric("マスク数", 0)

    # マスクオーバーレイプレビュー
    if masks_p.exists() and images_p.exists():
        mask_files = sorted(masks_p.glob("*.png"))
        if mask_files:
            st.subheader("マスクオーバーレイプレビュー")
            preview_idx = st.slider("プレビュー画像", 0, len(mask_files) - 1, 0)
            mask_file = mask_files[preview_idx]
            st.caption(f"マスク: {mask_file.name}")

            # 対応する元画像を探す
            stem = mask_file.stem
            src_img = None
            for ext in ("*.jpg", "*.png", "*.tif", "*.tiff"):
                candidates = list(images_p.glob(f"{stem}{ext[1:]}"))
                if candidates:
                    src_img = candidates[0]
                    break

            if src_img:
                try:
                    import cv2
                    import numpy as np
                    img = cv2.imread(str(src_img))
                    mask = cv2.imread(str(mask_file), cv2.IMREAD_GRAYSCALE)
                    if img is not None and mask is not None:
                        mask_resized = cv2.resize(mask, (img.shape[1], img.shape[0]))
                        overlay = img.copy()
                        overlay[mask_resized > 127] = (
                            overlay[mask_resized > 127] * 0.5
                            + np.array([0, 0, 200], dtype=np.float64) * 0.5
                        ).astype(np.uint8)
                        overlay_rgb = cv2.cvtColor(overlay, cv2.COLOR_BGR2RGB)
                        st.image(overlay_rgb, caption=f"{src_img.name} + mask",
                                 use_container_width=True)
                except ImportError:
                    st.warning("プレビューには opencv-python と numpy が必要です")
            else:
                st.image(str(mask_file), caption=mask_file.name,
                         use_container_width=True)
else:
    st.info("サイドバーで作業ディレクトリを設定してください")
