@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title maimai-planner environment check

set "FAIL=0"
set "PY="

echo.
echo ============================================================
echo   maimai-planner environment check
echo ============================================================
echo.

rem ---------- Python ----------
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    where python3 >nul 2>nul
    if not errorlevel 1 set "PY=python3"
)

if not defined PY (
    echo [FAIL] Python not found.
    echo        Install Python 3.10 or newer and add it to PATH.
    set "FAIL=1"
) else (
    %PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [FAIL] Python is older than 3.10.
        %PY% -c "import sys; print('       Current: ' + sys.version.split()[0])"
        set "FAIL=1"
    ) else (
        echo [OK] Python
        %PY% -c "import sys; print('     Version: ' + sys.version.split()[0]); print('     Path: ' + sys.executable)"
    )
)

rem ---------- required Python packages ----------
echo.
echo [CHECK] Required Python packages
if defined PY (
    %PY% -c "import UnityPy" >nul 2>nul
    if errorlevel 1 (
        echo [FAIL] UnityPy is not installed.
        set "FAIL=1"
    ) else (
        %PY% -c "import UnityPy; print('     UnityPy: ' + str(getattr(UnityPy, '__version__', '?')))"
        echo [OK] UnityPy
    )

    %PY% -c "import PIL" >nul 2>nul
    if errorlevel 1 (
        echo [FAIL] Pillow is not installed.
        set "FAIL=1"
    ) else (
        %PY% -c "import PIL; print('     Pillow: ' + str(getattr(PIL, '__version__', '?')))"
        echo [OK] Pillow
    )
) else (
    echo [SKIP] Python is missing.
)

rem ---------- optional Windows helper ----------
echo.
echo [CHECK] Optional Windows helper
set "PS="
where powershell.exe >nul 2>nul
if not errorlevel 1 set "PS=powershell.exe"
if not defined PS (
    where pwsh.exe >nul 2>nul
    if not errorlevel 1 set "PS=pwsh.exe"
)
if not defined PS (
    where pwsh >nul 2>nul
    if not errorlevel 1 set "PS=pwsh"
)
if defined PS (
    echo [OK] PowerShell: %PS%
) else (
    echo [WARN] PowerShell not found; folder/file pickers will not work.
)

echo.
echo ============================================================
if "%FAIL%"=="1" (
    echo RESULT: environment check failed.
    echo Fix the failed items above, then run start.py.
    echo ============================================================
    pause
    exit /b 1
) else (
    echo RESULT: environment check passed.
    echo Required: Python 3.10 or newer, UnityPy, Pillow.
    echo ============================================================
    pause
    exit /b 0
)
