@echo off
chcp 65001 >nul
setlocal

REM === デュアル魚眼フレーム抽出ツール ===
REM 使い方: このバッチファイルに .osv/.insv ファイルをドラッグ＆ドロップ
REM         または引数なしで実行すると対話的にファイルを指定

set "SCRIPT_DIR=%~dp0"
set "INPUT=%~1"

if "%INPUT%"=="" (
    echo.
    echo === デュアル魚眼フレーム抽出ツール ===
    echo.
    set /p "INPUT=入力ファイルパス (.osv/.insv): "
)

if "%INPUT%"=="" (
    echo エラー: ファイルが指定されていません
    pause
    exit /b 1
)

python "%SCRIPT_DIR%extract_dual_fisheye.py" "%INPUT%" -o "%SCRIPT_DIR%frames" --quality 95

echo.
pause
