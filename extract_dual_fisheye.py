"""デュアル魚眼動画（.osv/.insv）からMetashape用フレーム画像を抽出するCLIツール"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

SUPPORTED_FORMATS = ("jpg", "png", "tiff")
FORMAT_EXT = {"jpg": "jpg", "png": "png", "tiff": "tif"}

ROTATION_OPTIONS = [0, 90, 180, 270]
TRANSPOSE_FILTERS = {
    0: None,
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}

# シャープネス評価の依存関係チェック
try:
    from PIL import Image
    import numpy as np
    _HAS_SHARP_DEPS = True
except ImportError:
    _HAS_SHARP_DEPS = False


def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def probe_streams(input_path: Path) -> list[dict]:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_entries", "stream=index,codec_name,width,height,r_frame_rate,nb_frames,pix_fmt,tags:stream_side_data",
        "-select_streams", "v",
        str(input_path),
    ]
    result = run_cmd(cmd)
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if len(streams) < 2:
        print(f"エラー: ビデオストリームが{len(streams)}個しか見つかりません（2個必要）", file=sys.stderr)
        sys.exit(1)
    return streams


def get_source_fps(stream: dict) -> float:
    """ストリームのソースFPSを取得"""
    r_fps = stream.get("r_frame_rate", "30/1")
    if "/" in r_fps:
        num, den = r_fps.split("/")
        return float(num) / float(den)
    return float(r_fps)


def get_duration(input_path: Path) -> float | None:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(input_path),
    ]
    result = run_cmd(cmd, check=False)
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    dur = data.get("format", {}).get("duration")
    return float(dur) if dur else None


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def get_rotation(stream: dict) -> int:
    tags = stream.get("tags", {})
    rotate = tags.get("rotate")
    if rotate is not None:
        return int(rotate) % 360
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            r = int(sd["rotation"])
            return (-r) % 360
    return 0


def print_stream_info(streams: list[dict]) -> None:
    labels = ["front (0:v:0)", "back  (0:v:1)"]
    for stream, label in zip(streams[:2], labels):
        w = stream.get("width", "?")
        h = stream.get("height", "?")
        codec = stream.get("codec_name", "?")
        r_fps = stream.get("r_frame_rate", "?")
        nb = stream.get("nb_frames", "?")
        pix_fmt = stream.get("pix_fmt", "?")
        rot = get_rotation(stream)
        rot_str = f", rotation={rot}°" if rot != 0 else ""
        print(f"  {label}: {w}x{h}, {codec}, {pix_fmt}, fps={r_fps}, frames={nb}{rot_str}")


def detect_bit_depth(streams: list[dict]) -> int:
    """ffprobeのpix_fmtからビット深度を推定"""
    for s in streams[:2]:
        pix_fmt = s.get("pix_fmt", "")
        if "10" in pix_fmt or "p10" in pix_fmt:
            return 10
        if "12" in pix_fmt or "p12" in pix_fmt:
            return 12
    return 8


def find_lut_file(search_dir: Path) -> Path | None:
    """指定ディレクトリから.cubeファイルを探す"""
    cube_files = sorted(search_dir.glob("*.cube"))
    if not cube_files:
        return None
    if len(cube_files) == 1:
        return cube_files[0]
    print("\n--- LUTファイル選択 ---")
    print(f"  {len(cube_files)} 個の .cube ファイルが見つかりました:")
    for i, f in enumerate(cube_files, 1):
        print(f"  [{i}] {f.name}")
    while True:
        ans = input(f"使用するLUTを選択 [1]: ").strip()
        if ans == "":
            return cube_files[0]
        try:
            idx = int(ans)
            if 1 <= idx <= len(cube_files):
                return cube_files[idx - 1]
        except ValueError:
            pass
        print(f"  1〜{len(cube_files)}の番号を入力してください。")


def calc_sharpness(image_path: Path, max_size: int = 512) -> float:
    """Laplacian分散によるシャープネス評価（値が大きいほどシャープ）"""
    img = Image.open(image_path).convert("L")
    # 評価速度のため縮小
    ratio = min(max_size / img.width, max_size / img.height, 1.0)
    if ratio < 1.0:
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
    arr = np.array(img, dtype=np.float64)
    # Laplacian（離散近似）
    lap = (arr[:-2, 1:-1] + arr[2:, 1:-1] +
           arr[1:-1, :-2] + arr[1:-1, 2:] -
           4 * arr[1:-1, 1:-1])
    return float(lap.var())


def prompt_format(bit_depth: int) -> tuple[str, int]:
    """出力形式と品質を対話的に選択。(fmt, quality)を返す"""
    print("\n--- 出力形式 ---")
    if bit_depth > 8:
        print(f"  ※ ソース映像は {bit_depth}bit です")
    print("  [1] JPEG  (8bit)  — ファイル小、一般用途")
    print("  [2] PNG   (16bit) — ロスレス、10bit色保持")
    print("  [3] TIFF  (16bit) — ロスレス、10bit色保持")
    if bit_depth > 8:
        default = "2"
        print(f"  ※ 10bit以上のソースにはPNG/TIFFを推奨")
    else:
        default = "1"

    while True:
        ans = input(f"出力形式を選択 [{default}]: ").strip()
        if ans == "":
            ans = default
        if ans == "1":
            while True:
                q_ans = input("JPEG品質 1-100 [95]: ").strip()
                if q_ans == "":
                    return "jpg", 95
                try:
                    q = int(q_ans)
                    if 1 <= q <= 100:
                        return "jpg", q
                except ValueError:
                    pass
                print("1〜100の数値を入力してください。")
        elif ans == "2":
            return "png", 95
        elif ans == "3":
            return "tiff", 95
        else:
            print("1〜3の番号を入力してください。")


def prompt_fps(duration: float | None, default_fps: float) -> float:
    print("\n--- FPS設定 ---")
    if duration:
        print(f"動画の長さ: {format_duration(duration)} ({duration:.1f}秒)")
    print(f"現在のFPS: {default_fps}")
    if duration:
        est = int(duration * default_fps)
        print(f"  → 予想抽出枚数: 約 {est} 枚/レンズ（合計 約 {est * 2} 枚）")
    print()
    print("FPS例:  0.5 = 2秒に1枚 / 1 = 毎秒1枚 / 2 = 毎秒2枚 / 5 = 毎秒5枚")

    while True:
        ans = input(f"抽出FPSを入力 [{default_fps}]: ").strip()
        if ans == "":
            fps = default_fps
        else:
            try:
                fps = float(ans)
                if fps <= 0:
                    print("正の数を入力してください。")
                    continue
            except ValueError:
                print("数値を入力してください。")
                continue

        if duration:
            est = int(duration * fps)
            print(f"  → 予想抽出枚数: 約 {est} 枚/レンズ（合計 約 {est * 2} 枚）")

        confirm = input("この設定で実行しますか？ [Y/n]: ").strip().lower()
        if confirm in ("", "y", "yes"):
            return fps
        print()


def build_vf(fps: float | None, rotation: int,
             lut_path: Path | None = None) -> str:
    """ffmpeg -vf フィルタチェーンを構築"""
    filters = []
    if fps is not None:
        filters.append(f"fps={fps}")
    if lut_path is not None:
        lut_str = str(lut_path).replace("\\", "/").replace(":", "\\:")
        filters.append(f"lut3d='{lut_str}'")
    t = TRANSPOSE_FILTERS.get(rotation)
    if t:
        filters.append(t)
    return ",".join(filters) if filters else "null"


def build_output_args(fmt: str, quality: int) -> list[str]:
    """出力形式に応じたffmpegオプションを返す"""
    if fmt == "png":
        return ["-pix_fmt", "rgb48be"]
    elif fmt == "tiff":
        return ["-pix_fmt", "rgb48le"]
    else:  # jpg
        return ["-q:v", str(max(1, min(31, (100 - quality) * 31 // 100 + 1)))]


def build_lut_filter(lut_path: Path | None) -> str | None:
    """LUTフィルタ文字列を生成"""
    if lut_path is None:
        return None
    lut_str = str(lut_path).replace("\\", "/").replace(":", "\\:")
    return f"lut3d='{lut_str}'"


def extract_test_frame(input_path: Path, test_dir: Path, stream_index: int,
                       label: str, rotation: int,
                       lut_path: Path | None = None) -> Path:
    """テスト用に1フレームを指定ストリーム・回転で抽出"""
    out_path = test_dir / f"{label}_{rotation}deg.jpg"
    vf = build_vf(None, rotation, lut_path)
    cmd = [
        "ffmpeg", "-v", "warning",
        "-noautorotate",
        "-i", str(input_path),
        "-map", f"0:v:{stream_index}",
        "-vf", vf,
        "-frames:v", "1",
        "-q:v", "5",
        "-update", "1",
        str(out_path),
        "-y",
    ]
    run_cmd(cmd)
    return out_path


def prompt_rotation_for_stream(input_path: Path, test_dir: Path,
                               stream_index: int, label: str,
                               lut_path: Path | None = None) -> int:
    """指定ストリームのテストフレームを4方向で抽出し、正しい向きを選ばせる"""
    print(f"\n  {label} のテストフレームを抽出中...")
    for rot in ROTATION_OPTIONS:
        extract_test_frame(input_path, test_dir, stream_index, label, rot,
                           lut_path)

    os.startfile(str(test_dir))

    print(f"  画像を確認してください（エクスプローラーが開きます）:")
    for i, rot in enumerate(ROTATION_OPTIONS, 1):
        print(f"    [{i}] {label}_{rot}deg.jpg （時計回りに{rot}°回転）")

    while True:
        ans = input(f"  {label} の正しい向き [1-4]: ").strip()
        if ans in ("1", "2", "3", "4"):
            chosen = ROTATION_OPTIONS[int(ans) - 1]
            print(f"    → {label}: {chosen}°回転")
            return chosen
        print("  1〜4の番号を入力してください。")


def prompt_rotation(input_path: Path,
                    lut_path: Path | None = None) -> tuple[int, int]:
    """front/backそれぞれのテストフレームで回転方向を確認"""
    print("\n--- 回転方向の確認 ---")
    print("front / back それぞれのテストフレームを抽出します...")

    test_dir = Path(tempfile.mkdtemp(prefix="fisheye_rot_"))
    try:
        rot_front = prompt_rotation_for_stream(input_path, test_dir, 0,
                                               "front", lut_path)
        rot_back = prompt_rotation_for_stream(input_path, test_dir, 1,
                                              "back", lut_path)

        print(f"\n  設定: front={rot_front}°, back={rot_back}°")
        while True:
            confirm = input("  この設定でよいですか？ [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                return rot_front, rot_back
            if confirm in ("n", "no"):
                print("  もう一度選択してください。")
                rot_front = prompt_rotation_for_stream(
                    input_path, test_dir, 0, "front", lut_path)
                rot_back = prompt_rotation_for_stream(
                    input_path, test_dir, 1, "back", lut_path)
                print(f"\n  設定: front={rot_front}°, back={rot_back}°")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def extract_frames_simple(
    input_path: Path,
    output_dir: Path,
    stream_index: int,
    prefix: str,
    fps: float,
    quality: int,
    rotation: int = 0,
    fmt: str = "jpg",
    lut_path: Path | None = None,
) -> int:
    """固定間隔でフレーム抽出（シャープネス選択なし）"""
    ext = FORMAT_EXT[fmt]
    tmp_pattern = output_dir / f"{prefix}_%04d.{ext}"
    vf = build_vf(fps, rotation, lut_path)
    cmd = [
        "ffmpeg", "-v", "warning",
        "-noautorotate",
        "-i", str(input_path),
        "-map", f"0:v:{stream_index}",
        "-vf", vf,
        *build_output_args(fmt, quality),
        "-start_number", "1",
        str(tmp_pattern),
    ]
    run_cmd(cmd)
    return len(list(output_dir.glob(f"{prefix}_*.{ext}")))


def extract_frames_sharp(
    input_path: Path,
    output_dir: Path,
    stream_index: int,
    prefix: str,
    target_fps: float,
    quality: int,
    rotation: int,
    fmt: str,
    lut_path: Path | None,
    source_fps: float,
) -> int:
    """シャープネス選択付きフレーム抽出

    1. ソースFPSで候補を低解像度JPEG抽出（評価用）
    2. ウィンドウ(1/target_fps秒)ごとにLaplacian分散で最シャープ候補を選択
    3. 選択フレームのみ高品質で再抽出（LUT・回転適用）
    """
    ext = FORMAT_EXT[fmt]

    # 候補FPS: ソースFPSそのまま、ただし1ウィンドウ30候補まで
    max_candidates = 30
    candidate_fps = min(source_fps, target_fps * max_candidates)
    frames_per_window = max(1, round(candidate_fps / target_fps))

    print(f"    シャープネス選択: {frames_per_window}候補/ウィンドウ "
          f"(候補{candidate_fps:.0f}fps)")

    with tempfile.TemporaryDirectory(prefix="fisheye_sharp_") as tmp_dir:
        tmp_path = Path(tmp_dir)

        # --- Pass 1: 候補を低解像度JPEGで抽出（評価用） ---
        print(f"    候補フレーム抽出中...")
        cand_pattern = tmp_path / "cand_%06d.jpg"
        cmd = [
            "ffmpeg", "-v", "warning",
            "-noautorotate",
            "-i", str(input_path),
            "-map", f"0:v:{stream_index}",
            "-vf", f"fps={candidate_fps},scale=512:-1",
            "-q:v", "5",
            "-start_number", "0",
            str(cand_pattern),
        ]
        run_cmd(cmd)

        candidates = sorted(tmp_path.glob("cand_*.jpg"))
        total_cand = len(candidates)
        if total_cand == 0:
            return 0

        # --- シャープネス評価 → ウィンドウごとに最良選択 ---
        num_windows = (total_cand + frames_per_window - 1) // frames_per_window
        print(f"    シャープネス評価中（{total_cand}候補 → {num_windows}枚選択）...")

        winner_indices = []  # 0-based index in candidate stream
        for win_idx in range(num_windows):
            start = win_idx * frames_per_window
            end = min(start + frames_per_window, total_cand)

            best_idx = start
            best_score = -1.0
            for i in range(start, end):
                score = calc_sharpness(candidates[i])
                if score > best_score:
                    best_score = score
                    best_idx = i
            winner_indices.append(best_idx)

            # 進捗表示
            if (win_idx + 1) % 20 == 0 or win_idx == num_windows - 1:
                pct = (win_idx + 1) * 100 // num_windows
                print(f"      {win_idx + 1}/{num_windows} ({pct}%)")

    # --- Pass 2: 選択フレームのみ高品質で抽出 ---
    print(f"    選択フレーム抽出中（{len(winner_indices)}枚）...")

    select_expr = "+".join(f"eq(n\\,{idx})" for idx in winner_indices)
    vf_parts = [f"fps={candidate_fps}", f"select={select_expr}"]
    lut_filter = build_lut_filter(lut_path)
    if lut_filter:
        vf_parts.append(lut_filter)
    t = TRANSPOSE_FILTERS.get(rotation)
    if t:
        vf_parts.append(t)
    vf = ",".join(vf_parts)

    out_pattern = output_dir / f"{prefix}_%04d.{ext}"
    cmd = [
        "ffmpeg", "-v", "warning",
        "-noautorotate",
        "-i", str(input_path),
        "-map", f"0:v:{stream_index}",
        "-vf", vf,
        "-vsync", "vfr",
        *build_output_args(fmt, quality),
        "-start_number", "1",
        str(out_pattern),
    ]
    run_cmd(cmd)
    return len(list(output_dir.glob(f"{prefix}_*.{ext}")))


def rename_frames(output_dir: Path, prefix: str, count: int,
                  fmt: str, start_seq: int) -> int:
    """フレームを連番にリネーム。次の連番を返す"""
    ext = FORMAT_EXT[fmt]
    seq = start_seq
    for i in range(1, count + 1):
        src = output_dir / f"{prefix}_{i:04d}.{ext}"
        dst = output_dir / f"{prefix}_{i:04d}_{seq}.{ext}"
        if src.exists():
            src.rename(dst)
            seq += 1
    return seq


def resolve_rotation(streams: list[dict], input_path: Path,
                     args_rotate_front: int | None,
                     args_rotate_back: int | None,
                     lut_path: Path | None = None) -> tuple[int, int]:
    """メタデータ → 引数 → 対話の優先順で回転を決定"""
    meta_rot_front = get_rotation(streams[0])
    meta_rot_back = get_rotation(streams[1])

    if meta_rot_front or meta_rot_back:
        print(f"  回転補正（メタデータ検出）: front={meta_rot_front}°, back={meta_rot_back}°")
        return meta_rot_front, meta_rot_back
    elif args_rotate_front is not None and args_rotate_back is not None:
        print(f"  回転補正（引数指定）: front={args_rotate_front}°, back={args_rotate_back}°")
        return args_rotate_front, args_rotate_back
    else:
        return prompt_rotation(input_path, lut_path)


def process_one_video(
    input_path: Path,
    output_dir: Path,
    video_index: int,
    fps: float,
    quality: int,
    fmt: str,
    rot_front: int,
    rot_back: int,
    start_seq: int,
    lut_path: Path | None = None,
    use_sharp: bool = True,
    source_fps: float = 30.0,
) -> tuple[int, int, int]:
    """1動画を処理。(front_count, back_count, next_seq)を返す"""
    front_prefix = f"v{video_index}_front"
    back_prefix = f"v{video_index}_back"

    if use_sharp:
        extract_fn = lambda si, pf, rot: extract_frames_sharp(
            input_path, output_dir, si, pf, fps, quality, rot, fmt,
            lut_path, source_fps)
    else:
        extract_fn = lambda si, pf, rot: extract_frames_simple(
            input_path, output_dir, si, pf, fps, quality, rot, fmt, lut_path)

    print(f"  front (0:v:0) 抽出中...")
    front_count = extract_fn(0, front_prefix, rot_front)
    print(f"  front: {front_count} フレーム")

    print(f"  back  (0:v:1) 抽出中...")
    back_count = extract_fn(1, back_prefix, rot_back)
    print(f"  back:  {back_count} フレーム")

    next_seq = rename_frames(output_dir, front_prefix, front_count, fmt, start_seq)
    next_seq = rename_frames(output_dir, back_prefix, back_count, fmt, next_seq)

    return front_count, back_count, next_seq


def main():
    parser = argparse.ArgumentParser(
        description="デュアル魚眼動画（.osv/.insv）からMetashape用フレーム画像を抽出"
    )
    parser.add_argument("input", type=Path, nargs="+",
                        help=".osv または .insv ファイルパス（複数指定可）")
    parser.add_argument("-o", "--output", type=Path, default=Path("./frames"),
                        help="出力ディレクトリ（デフォルト: ./frames）")
    parser.add_argument("--fps", type=float, default=None,
                        help="抽出FPS（省略時は対話的に設定）")
    parser.add_argument("--quality", type=int, default=None,
                        help="JPEG品質 1-100（省略時は対話的に設定）")
    parser.add_argument("--format", type=str, default=None,
                        choices=SUPPORTED_FORMATS,
                        help="出力形式: jpg(8bit) / png(16bit) / tiff(16bit)（省略時は対話的に設定）")
    parser.add_argument("--lut", type=Path, default=None,
                        help=".cube LUTファイルパス（デフォルト: スクリプトと同じフォルダから自動検出）")
    parser.add_argument("--no-lut", action="store_true",
                        help="LUT適用をスキップ（フラットな色のまま抽出）")
    parser.add_argument("--no-sharp", action="store_true",
                        help="シャープネス選択を無効化（固定間隔で抽出）")
    parser.add_argument("--rotate-front", type=int, default=None, choices=[0, 90, 180, 270],
                        help="front回転角度を直接指定")
    parser.add_argument("--rotate-back", type=int, default=None, choices=[0, 90, 180, 270],
                        help="back回転角度を直接指定")
    args = parser.parse_args()

    input_files = args.input
    for f in input_files:
        if not f.exists():
            print(f"エラー: 入力ファイルが見つかりません: {f}", file=sys.stderr)
            sys.exit(1)

    # シャープネス選択の有効/無効判定
    use_sharp = not args.no_sharp
    if use_sharp and not _HAS_SHARP_DEPS:
        print("警告: シャープネス選択には Pillow と NumPy が必要です")
        print("  pip install Pillow numpy")
        print("  → 固定間隔抽出にフォールバックします")
        use_sharp = False

    if len(input_files) > 1:
        print(f"\n=== {len(input_files)} 本の動画を連番で抽出します ===")
        for i, f in enumerate(input_files, 1):
            print(f"  [{i}] {f.name}")

    # 1. 最初の動画でストリーム情報取得・設定決定
    print(f"\n入力: {input_files[0]}")
    streams = probe_streams(input_files[0])
    print("ストリーム情報:")
    print_stream_info(streams)

    source_fps = get_source_fps(streams[0])

    # 2. LUT設定（デフォルトON）
    lut_path = None
    if args.no_lut:
        print("\nLUT: スキップ（--no-lut 指定）")
    elif args.lut is not None:
        if not args.lut.exists():
            print(f"エラー: LUTファイルが見つかりません: {args.lut}", file=sys.stderr)
            sys.exit(1)
        lut_path = args.lut.resolve()
        print(f"\nLUT: {lut_path.name}")
    else:
        lut_path = find_lut_file(SCRIPT_DIR)
        if lut_path is not None:
            lut_path = lut_path.resolve()
            print(f"\nLUT: {lut_path.name}（自動検出）")
        else:
            print(f"\n警告: .cube LUTファイルが見つかりません")
            print(f"  検索先: {SCRIPT_DIR}")
            print(f"  D-Log M動画の場合、DJI公式から「D-Log M to Rec.709」LUTを")
            print(f"  ダウンロードして、このスクリプトと同じフォルダに置いてください。")
            while True:
                ans = input("LUTなしで続行しますか？ [Y/n]: ").strip().lower()
                if ans in ("", "y", "yes"):
                    break
                if ans in ("n", "no"):
                    print("中断しました。LUTファイルを配置してから再実行してください。")
                    sys.exit(0)

    # 3. 出力形式の決定（全動画共通）
    bit_depth = detect_bit_depth(streams)
    if args.format is not None:
        fmt = args.format
        quality = args.quality if args.quality is not None else 95
    else:
        fmt, quality = prompt_format(bit_depth)

    # 4. FPS設定（全動画共通）
    duration = get_duration(input_files[0])
    if len(input_files) > 1:
        total_dur = 0.0
        for f in input_files:
            d = get_duration(f)
            if d:
                total_dur += d
        if total_dur > 0:
            print(f"\n全動画の合計時間: {format_duration(total_dur)} ({total_dur:.1f}秒)")

    if args.fps is not None:
        fps = args.fps
        if duration:
            est = int(duration * fps)
            print(f"\n動画の長さ: {format_duration(duration)} ({duration:.1f}秒)")
            print(f"FPS: {fps} → 予想抽出枚数: 約 {est} 枚/レンズ（合計 約 {est * 2} 枚）")
    else:
        fps = prompt_fps(duration, default_fps=1.0)

    # 5. 出力ディレクトリ準備
    args.output.mkdir(parents=True, exist_ok=True)

    # 6. 各動画を順番に処理
    fmt_label = {"jpg": "JPEG 8bit", "png": "PNG 16bit", "tiff": "TIFF 16bit"}[fmt]
    lut_label = lut_path.name if lut_path else "なし"
    sharp_label = "ON" if use_sharp else "OFF"
    global_seq = 0
    total_front = 0
    total_back = 0

    for vid_idx, input_path in enumerate(input_files, 1):
        print(f"\n{'='*50}")
        print(f"動画 [{vid_idx}/{len(input_files)}]: {input_path.name}")
        print(f"{'='*50}")

        suffix = input_path.suffix.lower()
        if suffix not in (".osv", ".insv"):
            print(f"警告: 拡張子 '{suffix}' は想定外です（.osv / .insv を期待）", file=sys.stderr)

        if vid_idx > 1:
            streams = probe_streams(input_path)
            print("ストリーム情報:")
            print_stream_info(streams)
            source_fps = get_source_fps(streams[0])

        rot_front, rot_back = resolve_rotation(
            streams, input_path, args.rotate_front, args.rotate_back, lut_path)

        print(f"\nフレーム抽出中（fps={fps}, format={fmt_label}, "
              f"LUT={lut_label}, シャープ選択={sharp_label}）...")
        print(f"  回転: front={rot_front}°, back={rot_back}°")
        if global_seq > 0:
            print(f"  連番開始: {global_seq} から")

        front_count, back_count, global_seq = process_one_video(
            input_path, args.output, vid_idx, fps, quality, fmt,
            rot_front, rot_back, global_seq, lut_path, use_sharp, source_fps)

        total_front += front_count
        total_back += back_count

    # 7. サマリー
    total = total_front + total_back
    w = streams[0].get("width", "?")
    h = streams[0].get("height", "?")

    print(f"\n{'='*50}")
    print(f"完了!")
    if len(input_files) > 1:
        print(f"  動画数: {len(input_files)} 本")
    print(f"  抽出枚数: {total} ({total_front} front + {total_back} back)")
    print(f"  解像度: {w}x{h}")
    print(f"  出力形式: {fmt_label}")
    print(f"  LUT: {lut_label}")
    print(f"  シャープネス選択: {sharp_label}")
    print(f"  連番範囲: 0 〜 {global_seq - 1}")
    print(f"  出力先: {args.output.resolve()}")


if __name__ == "__main__":
    main()
