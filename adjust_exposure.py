"""
魚眼フレームの露出補正

抽出済み魚眼フレームの白飛び・暗すぎを検出し、ガンマ補正で露出を調整する。
全天球画像と異なり空検出やソフトマスクは不要なため、グローバルガンマ補正のみ。

用途: 露出固定撮影で暗所→明所に出たときの白飛びフレームを抑制し、
3DGSトレーニング時のフローター発生を予防する。

パイプライン挿入位置:
  フレーム抽出 → **露出補正（任意）** → マスク生成

補正前の画像は frames_backup_exposure/ にバックアップ（復元可能）。
"""

import argparse
import csv
import shutil
import sys
from pathlib import Path

try:
    import cv2
    import numpy as np
    _HAS_DEPS = True
except ImportError:
    _HAS_DEPS = False


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def analyze_brightness(img: np.ndarray) -> dict:
    """フレームの輝度統計を計算する。

    Returns:
        mean: 平均輝度
        median: 中央値
        p95: 95パーセンタイル
        overexposed_ratio: 白飛び画素率 (>= 245)
        underexposed_ratio: 黒潰れ画素率 (<= 10)
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    total_pixels = gray.size

    return {
        "mean": float(np.mean(gray)),
        "median": float(np.median(gray)),
        "p95": float(np.percentile(gray, 95)),
        "overexposed_ratio": float(np.sum(gray >= 245)) / total_pixels,
        "underexposed_ratio": float(np.sum(gray <= 10)) / total_pixels,
    }


# ---------------------------------------------------------------------------
# 補正
# ---------------------------------------------------------------------------

def compute_gamma(region_mean: float, target_mean: float) -> float | None:
    """平均輝度からガンマ補正値を計算する。"""
    if region_mean < 1.0:
        region_mean = 1.0
    if target_mean <= 0.0 or region_mean / 255.0 <= 0.0:
        return None

    gamma = np.log(target_mean / 255.0) / np.log(region_mean / 255.0)
    return float(np.clip(gamma, 0.3, 3.0))


def apply_gamma(img: np.ndarray, gamma: float) -> np.ndarray:
    """画像全体にガンマ補正を適用する。"""
    lut = np.array(
        [((i / 255.0) ** gamma) * 255 for i in range(256)],
        dtype=np.uint8,
    )
    return cv2.LUT(img, lut)


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def run_adjust_exposure(
    frames_dir: str,
    bright_mean_threshold: float = 180.0,
    bright_overexposed_min: float = 0.10,
    dark_threshold: float = 60.0,
    bright_target_mean: float = 130.0,
    dark_target_mean: float = 100.0,
    dry_run: bool = False,
) -> int:
    """魚眼フレームの露出補正を実行する。

    Args:
        frames_dir: フレーム画像のディレクトリ
        bright_mean_threshold: 平均輝度がこれ以上で白飛び候補
        bright_overexposed_min: 白飛び画素率がこれ以上で白飛び候補
        dark_threshold: 平均輝度がこれ以下で暗すぎ
        bright_target_mean: 白飛びフレームの補正目標輝度
        dark_target_mean: 暗フレームの補正目標輝度
        dry_run: True なら分析のみ

    白飛び判定（2条件の両方を満たす場合）:
      1. 平均輝度 >= bright_mean_threshold
      2. 白飛び画素率 >= bright_overexposed_min
    """
    if not _HAS_DEPS:
        print("エラー: opencv-python と numpy が必要です")
        print("  pip install opencv-python numpy")
        return 1

    fdir = Path(frames_dir)
    backup_dir = fdir.parent / f"{fdir.name}_backup_exposure"

    if not fdir.exists():
        print(f"エラー: {fdir} が見つかりません")
        return 1

    # 画像ファイル収集
    extensions = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    image_paths = sorted(
        f for f in fdir.iterdir() if f.suffix.lower() in extensions
    )

    total = len(image_paths)
    print(f"対象フレーム数: {total}")
    if total == 0:
        print("フレームがありません")
        return 1

    # 分析
    print("輝度分析中...")
    results = []
    for i, path in enumerate(image_paths):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            print(f"  警告: 読み込めません: {path.name}")
            continue

        stats = analyze_brightness(img)

        # 判定
        status = "NORMAL"
        gamma = None

        is_bright = (
            stats["mean"] >= bright_mean_threshold
            and stats["overexposed_ratio"] >= bright_overexposed_min
        )
        is_dark = stats["mean"] <= dark_threshold

        if is_bright:
            status = "OVEREXPOSED"
            gamma = compute_gamma(stats["mean"], bright_target_mean)
        elif is_dark:
            status = "UNDEREXPOSED"
            gamma = compute_gamma(stats["mean"], dark_target_mean)

        results.append({
            "filename": path.name,
            "path": str(path),
            "mean": stats["mean"],
            "median": stats["median"],
            "p95": stats["p95"],
            "overexposed_ratio": stats["overexposed_ratio"],
            "underexposed_ratio": stats["underexposed_ratio"],
            "status": status,
            "gamma": gamma,
        })

        if (i + 1) % 50 == 0 or (i + 1) == total:
            print(f"  分析: {i + 1}/{total}")

    # 統計表示
    means = np.array([r["mean"] for r in results])
    print(f"\n輝度分布:")
    print(f"  最小: {means.min():.1f}  最大: {means.max():.1f}")
    print(f"  平均: {means.mean():.1f}  中央値: {np.median(means):.1f}")

    bright_frames = [r for r in results if r["status"] == "OVEREXPOSED"]
    dark_frames = [r for r in results if r["status"] == "UNDEREXPOSED"]
    normal_frames = [r for r in results if r["status"] == "NORMAL"]

    print(f"\n判定結果:")
    print(f"  白飛び (OVEREXPOSED):  {len(bright_frames)}枚")
    print(f"  暗すぎ (UNDEREXPOSED): {len(dark_frames)}枚")
    print(f"  正常   (NORMAL):       {len(normal_frames)}枚")

    if bright_frames:
        gammas = [r["gamma"] for r in bright_frames if r["gamma"] is not None]
        if gammas:
            print(f"  白飛び補正ガンマ範囲: {min(gammas):.3f} ~ {max(gammas):.3f}")
    if dark_frames:
        gammas = [r["gamma"] for r in dark_frames if r["gamma"] is not None]
        if gammas:
            print(f"  暗部補正ガンマ範囲: {min(gammas):.3f} ~ {max(gammas):.3f}")

    to_adjust = [
        r for r in results
        if r["status"] != "NORMAL" and r["gamma"] is not None
    ]

    if not to_adjust:
        print("\n補正対象なし。")
        _write_csv(fdir.parent, fdir.name, results)
        return 0

    if dry_run:
        print(f"\n[DRY RUN] {len(to_adjust)}枚が補正対象です。"
              f"--dry-run を外して実行してください。")
        _write_csv(fdir.parent, fdir.name, results)
        return 0

    # バックアップ & 補正
    backup_dir.mkdir(exist_ok=True)
    print(f"\n補正実行中... (バックアップ先: {backup_dir})")

    adjusted_count = 0
    for i, r in enumerate(to_adjust):
        src = Path(r["path"])

        # バックアップ（まだ存在しなければ）
        bak = backup_dir / r["filename"]
        if not bak.exists():
            shutil.copy2(str(src), str(bak))

        img = cv2.imread(str(src), cv2.IMREAD_COLOR)
        if img is None:
            continue

        corrected = apply_gamma(img, r["gamma"])
        cv2.imwrite(str(src), corrected)
        adjusted_count += 1

        if (i + 1) % 20 == 0 or (i + 1) == len(to_adjust):
            print(f"  補正: {i + 1}/{len(to_adjust)}")

    print(f"\n完了: {adjusted_count}枚を補正しました")
    print(f"  バックアップ: {backup_dir}")
    _write_csv(fdir.parent, fdir.name, results)
    return 0


def _write_csv(base_dir: Path, frames_name: str, results: list[dict]):
    """分析結果をCSVに保存する。"""
    csv_path = base_dir / f"{frames_name}_exposure_report.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "filename", "mean", "median", "p95",
            "overexposed_ratio", "underexposed_ratio",
            "status", "gamma",
        ])
        for r in sorted(results, key=lambda x: x["filename"]):
            writer.writerow([
                r["filename"],
                f"{r['mean']:.1f}",
                f"{r['median']:.1f}",
                f"{r['p95']:.1f}",
                f"{r['overexposed_ratio']:.4f}",
                f"{r['underexposed_ratio']:.4f}",
                r["status"],
                f"{r['gamma']:.3f}" if r["gamma"] is not None else "",
            ])
    print(f"レポート保存: {csv_path}")


def restore_originals(frames_dir: str) -> int:
    """バックアップから元画像を復元する。"""
    fdir = Path(frames_dir)
    backup_dir = fdir.parent / f"{fdir.name}_backup_exposure"

    if not backup_dir.exists():
        print(f"{backup_dir.name} がありません。復元するものはありません。")
        return 0

    count = 0
    for f in backup_dir.iterdir():
        dst = fdir / f.name
        shutil.copy2(str(f), str(dst))
        count += 1

    print(f"{count}枚を復元しました。")
    print(f"バックアップは {backup_dir} に残してあります。不要なら手動で削除してください。")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="魚眼フレーム露出自動補正（白飛び抑制・暗部補正）"
    )
    parser.add_argument("--frames-dir", "-d", required=True,
                        help="フレーム画像ディレクトリ")
    parser.add_argument("--bright-mean", type=float, default=180.0,
                        help="平均輝度がこれ以上で白飛び候補 (default: 180)")
    parser.add_argument("--bright-overexposed-min", type=float, default=0.10,
                        help="白飛び画素率しきい値 (default: 0.10)")
    parser.add_argument("--dark-threshold", type=float, default=60.0,
                        help="平均輝度がこれ以下で暗すぎ (default: 60)")
    parser.add_argument("--bright-target-mean", type=float, default=130.0,
                        help="白飛きフレームの補正目標輝度 (default: 130)")
    parser.add_argument("--dark-target-mean", type=float, default=100.0,
                        help="暗フレームの補正目標輝度 (default: 100)")
    parser.add_argument("--dry-run", action="store_true",
                        help="分析のみ（画像は変更しない）")
    parser.add_argument("--restore", action="store_true",
                        help="バックアップから元画像を復元する")

    args = parser.parse_args()

    if args.restore:
        sys.exit(restore_originals(args.frames_dir))
    else:
        sys.exit(run_adjust_exposure(
            args.frames_dir,
            bright_mean_threshold=args.bright_mean,
            bright_overexposed_min=args.bright_overexposed_min,
            dark_threshold=args.dark_threshold,
            bright_target_mean=args.bright_target_mean,
            dark_target_mean=args.dark_target_mean,
            dry_run=args.dry_run,
        ))
