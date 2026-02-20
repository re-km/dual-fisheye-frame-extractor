"""デュアル魚眼動画（.osv/.insv）からMetashape用フレーム画像を抽出するCLIツール"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROTATION_OPTIONS = [0, 90, 180, 270]
TRANSPOSE_FILTERS = {
    0: None,
    90: "transpose=1",
    180: "transpose=1,transpose=1",
    270: "transpose=2",
}


def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def probe_streams(input_path: Path) -> list[dict]:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_entries", "stream=index,codec_name,width,height,r_frame_rate,nb_frames,tags:stream_side_data",
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
        rot = get_rotation(stream)
        rot_str = f", rotation={rot}°" if rot != 0 else ""
        print(f"  {label}: {w}x{h}, {codec}, fps={r_fps}, frames={nb}{rot_str}")


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


def build_vf(fps: float | None, rotation: int) -> str:
    filters = []
    if fps is not None:
        filters.append(f"fps={fps}")
    t = TRANSPOSE_FILTERS.get(rotation)
    if t:
        filters.append(t)
    return ",".join(filters) if filters else "null"


def extract_test_frame(input_path: Path, test_dir: Path, stream_index: int,
                       label: str, rotation: int) -> Path:
    """テスト用に1フレームを指定ストリーム・回転で抽出"""
    out_path = test_dir / f"{label}_{rotation}deg.jpg"
    vf = build_vf(None, rotation)
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
                               stream_index: int, label: str) -> int:
    """指定ストリームのテストフレームを4方向で抽出し、正しい向きを選ばせる"""
    print(f"\n  {label} のテストフレームを抽出中...")
    for rot in ROTATION_OPTIONS:
        extract_test_frame(input_path, test_dir, stream_index, label, rot)

    # エクスプローラーで画像を開いて確認できるようにする
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


def prompt_rotation(input_path: Path) -> tuple[int, int]:
    """front/backそれぞれのテストフレームで回転方向を確認"""
    print("\n--- 回転方向の確認 ---")
    print("front / back それぞれのテストフレームを抽出します...")

    test_dir = Path(tempfile.mkdtemp(prefix="fisheye_rot_"))
    try:
        rot_front = prompt_rotation_for_stream(input_path, test_dir, 0, "front")
        rot_back = prompt_rotation_for_stream(input_path, test_dir, 1, "back")

        print(f"\n  設定: front={rot_front}°, back={rot_back}°")
        while True:
            confirm = input("  この設定でよいですか？ [Y/n]: ").strip().lower()
            if confirm in ("", "y", "yes"):
                return rot_front, rot_back
            if confirm in ("n", "no"):
                # やり直し
                print("  もう一度選択してください。")
                rot_front = prompt_rotation_for_stream(input_path, test_dir, 0, "front")
                rot_back = prompt_rotation_for_stream(input_path, test_dir, 1, "back")
                print(f"\n  設定: front={rot_front}°, back={rot_back}°")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)


def extract_frames(
    input_path: Path,
    output_dir: Path,
    stream_index: int,
    prefix: str,
    fps: float,
    quality: int,
    rotation: int = 0,
) -> int:
    tmp_pattern = output_dir / f"{prefix}_%04d.jpg"
    vf = build_vf(fps, rotation)
    cmd = [
        "ffmpeg", "-v", "warning",
        "-noautorotate",
        "-i", str(input_path),
        "-map", f"0:v:{stream_index}",
        "-vf", vf,
        "-q:v", str(max(1, min(31, (100 - quality) * 31 // 100 + 1))),
        "-start_number", "1",
        str(tmp_pattern),
    ]
    run_cmd(cmd)
    return len(list(output_dir.glob(f"{prefix}_*.jpg")))


def rename_frames(output_dir: Path, front_count: int, back_count: int) -> None:
    global_seq = 0
    for i in range(1, front_count + 1):
        src = output_dir / f"front_{i:04d}.jpg"
        dst = output_dir / f"front_{i:04d}_{global_seq}.jpg"
        if src.exists():
            src.rename(dst)
            global_seq += 1
    for i in range(1, back_count + 1):
        src = output_dir / f"back_{i:04d}.jpg"
        dst = output_dir / f"back_{i:04d}_{global_seq}.jpg"
        if src.exists():
            src.rename(dst)
            global_seq += 1


def main():
    parser = argparse.ArgumentParser(
        description="デュアル魚眼動画（.osv/.insv）からMetashape用フレーム画像を抽出"
    )
    parser.add_argument("input", type=Path, help=".osv または .insv ファイルパス")
    parser.add_argument("-o", "--output", type=Path, default=Path("./frames"),
                        help="出力ディレクトリ（デフォルト: ./frames）")
    parser.add_argument("--fps", type=float, default=None,
                        help="抽出FPS（省略時は対話的に設定）")
    parser.add_argument("--quality", type=int, default=95,
                        help="JPEG品質 1-100（デフォルト: 95）")
    parser.add_argument("--rotate-front", type=int, default=None, choices=[0, 90, 180, 270],
                        help="front回転角度を直接指定")
    parser.add_argument("--rotate-back", type=int, default=None, choices=[0, 90, 180, 270],
                        help="back回転角度を直接指定")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"エラー: 入力ファイルが見つかりません: {args.input}", file=sys.stderr)
        sys.exit(1)

    suffix = args.input.suffix.lower()
    if suffix not in (".osv", ".insv"):
        print(f"警告: 拡張子 '{suffix}' は想定外です（.osv / .insv を期待）", file=sys.stderr)

    # 1. ストリーム情報取得
    print(f"入力: {args.input}")
    streams = probe_streams(args.input)
    print("ストリーム情報:")
    print_stream_info(streams)

    # 2. 動画の長さを取得
    duration = get_duration(args.input)

    # 3. FPS設定
    if args.fps is not None:
        fps = args.fps
        if duration:
            est = int(duration * fps)
            print(f"\n動画の長さ: {format_duration(duration)} ({duration:.1f}秒)")
            print(f"FPS: {fps} → 予想抽出枚数: 約 {est} 枚/レンズ（合計 約 {est * 2} 枚）")
    else:
        fps = prompt_fps(duration, default_fps=1.0)

    # 4. 回転補正の決定（front/back独立）
    meta_rot_front = get_rotation(streams[0])
    meta_rot_back = get_rotation(streams[1])

    if meta_rot_front or meta_rot_back:
        rot_front = meta_rot_front
        rot_back = meta_rot_back
        print(f"\n回転補正（メタデータ検出）: front={rot_front}°, back={rot_back}°")
    elif args.rotate_front is not None and args.rotate_back is not None:
        rot_front = args.rotate_front
        rot_back = args.rotate_back
        print(f"\n回転補正（引数指定）: front={rot_front}°, back={rot_back}°")
    else:
        rot_front, rot_back = prompt_rotation(args.input)

    # 5. 出力ディレクトリ準備
    args.output.mkdir(parents=True, exist_ok=True)

    # 6. フレーム抽出
    print(f"\nフレーム抽出中（fps={fps}, quality={args.quality}）...")
    print(f"  回転: front={rot_front}°, back={rot_back}°")

    print("  front (0:v:0) 抽出中...")
    front_count = extract_frames(args.input, args.output, 0, "front", fps, args.quality, rot_front)
    print(f"  front: {front_count} フレーム")

    print("  back  (0:v:1) 抽出中...")
    back_count = extract_frames(args.input, args.output, 1, "back", fps, args.quality, rot_back)
    print(f"  back:  {back_count} フレーム")

    # 7. リネーム
    print("\nMetashape命名規則にリネーム中...")
    rename_frames(args.output, front_count, back_count)

    # 8. サマリー
    total = front_count + back_count
    w = streams[0].get("width", "?")
    h = streams[0].get("height", "?")

    print(f"\n完了!")
    print(f"  抽出枚数: {total} ({front_count} front + {back_count} back)")
    print(f"  解像度: {w}x{h}")
    print(f"  回転補正: front={rot_front}°, back={rot_back}°")
    print(f"  出力先: {args.output.resolve()}")


if __name__ == "__main__":
    main()
