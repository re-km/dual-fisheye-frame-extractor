@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM === デュアル魚眼フレーム抽出ツール ===
REM 使い方: このバッチファイルに .osv/.insv ファイルをドラッグ＆ドロップ
REM         複数ファイルを同時にドロップすると連番で抽出

set "SCRIPT_DIR=%~dp0"

REM ドラッグ＆ドロップされた全ファイルを収集
set "FILES="
set "COUNT=0"

:collect_args
if "%~1"=="" goto check_files
set "FILES=!FILES! "%~1""
set /a COUNT+=1
shift
goto collect_args

:check_files
if %COUNT% equ 0 (
    echo.
    echo === デュアル魚眼フレーム抽出ツール ===
    echo.
    set /p "INPUT=入力ファイルパス (.osv/.insv): "
    if "!INPUT!"=="" (
        echo エラー: ファイルが指定されていません
        pause
        exit /b 1
    )
    set "FILES="!INPUT!""
)

python "%SCRIPT_DIR%extract_dual_fisheye.py" %FILES% -o "%SCRIPT_DIR%frames"

echo.
pause
