@echo off
setlocal EnableExtensions
title telegram-mcp recovery (2026-08-20)

set "REPO=B:\for-hermes\telegram-mcp"
set "BASE=f40f33d"
set "PATCH=RECOVERY-2026-08-20.patch"

echo.
echo === STEP 0: environment check ===
where git >nul 2>&1 || goto fail git-not-found
where python >nul 2>&1 || goto fail python-not-found
where uv >nul 2>&1 || goto fail uv-not-found
git --version
python --version
uv --version

cd /d "%REPO%" || goto fail folder-not-found

echo.
echo === STEP 1: check git repo and clean base commit ===
git rev-parse --is-inside-work-tree >nul 2>&1 || goto fail not-a-repo
git cat-file -e %BASE% 2>nul || goto fail base-not-found

echo.
echo === STEP 2: current local state ===
echo WARNING: next steps RESET tracked files and REMOVE untracked
echo non-ignored files in this folder. Check the status below.
git status --short
echo.
echo Press any key to continue, or close the window to abort...
pause >nul || goto fail user-aborted

echo.
echo === STEP 3: reset to clean base + remove leftover patch files ===
git reset --hard %BASE% || goto fail reset-failed
git clean -nd
git clean -fd || goto fail clean-failed

echo.
echo === STEP 4: check patch file exists and is valid UTF-8 ===
if not exist "%PATCH%" goto fail patch-missing
python -c "d=open(r'%PATCH%','rb').read(); d.decode('utf-8'); print('patch OK')" || goto fail patch-corrupt

echo.
echo === STEP 5: apply patch (dry-run, then real) ===
git apply --check "%PATCH%" || goto fail apply-check-failed
git apply "%PATCH%" || goto fail apply-failed
echo Patch applied.

echo.
echo === STEP 6: install dependencies + run all tests ===
echo Expect: 372 passed, 0 failed
uv sync || goto fail uv-sync-failed
uv run --with pytest --with pytest-asyncio pytest tests/ -q
if errorlevel 1 goto fail tests-failed

echo.
echo ============================================================
echo SUCCESS: working tree rebuilt, all tests passed.
echo Start the MCP server now (Hermes launcher, or: uv run main.py)
echo ============================================================
echo.
pause
exit /b 0


rem ================= error handlers (window stays open) =================

:fail
echo.
echo ============================================================
echo [STOPPED] reason code: %~1
echo ============================================================
echo.
if "%~1"=="git-not-found" echo Git is not installed or not on PATH. Install from https://git-scm.com and re-run.
if "%~1"=="python-not-found" echo Python is not on PATH. Install from https://python.org (check "Add to PATH") and re-run.
if "%~1"=="uv-not-found" echo uv is not on PATH. Install: pip install uv  (or https://docs.astral.sh/uv/) and re-run.
if "%~1"=="folder-not-found" echo Folder not found: %REPO%  - check the REPO= line at the top of this script.
if "%~1"=="not-a-repo" echo %REPO% is not a git repository. Clone it first:
if "%~1"=="not-a-repo" echo   git clone https://github.com/m07o/telegram-mcp.git "%REPO%"
if "%~1"=="base-not-found" echo Commit %BASE% not found in this repo. Try: git fetch origin   then re-run.
if "%~1"=="reset-failed" echo git reset failed. Scroll up and read the git error.
if "%~1"=="clean-failed" echo git clean failed. Scroll up and read the git error.
if "%~1"=="patch-missing" echo %PATCH% not found in %REPO% - put the patch file in this folder and re-run.
if "%~1"=="patch-corrupt" echo Patch file is corrupted (bad encoding). Re-download RECOVERY-2026-08-20.patch and re-run.
if "%~1"=="apply-check-failed" echo Dry-run failed. Scroll up: git usually prints which file/hunk failed.
if "%~1"=="apply-failed" echo git apply failed. Scroll up and read the git error.
if "%~1"=="uv-sync-failed" echo uv sync failed. Scroll up and read the uv error (common: network or lock file).
if "%~1"=="tests-failed" echo Some tests failed. Scroll up, find the red FAILED lines, and send them.
if "%~1"=="user-aborted" echo Aborted.
echo.
echo If the reason code is new or unclear, screenshot this window.
echo.
pause
exit /b 1