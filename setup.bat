@echo off
rem One-time setup for DiscTuner: checks Python, installs DiscTuner's packages,
rem fetches disc-probe next to this folder, and reports whether DiscImageStudio is installed.
setlocal
cd /d "%~dp0"

echo DiscTuner setup
echo.

rem 1. Python 3.11 or newer
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [!] Python 3.11 or newer was not found.
    echo     Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    echo     Then open a new window and run setup.bat again.
    exit /b 1
)
echo [ok] Python found:
python --version

rem 2. Python packages DiscTuner needs (disc-probe uses the same ones)
echo.
echo Installing Python packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [!] pip failed. Read the error above, fix it, then run setup.bat again.
    exit /b 1
)

rem 3. disc-probe, cloned next to the DiscTuner folder
for %%I in ("%~dp0..\disc-probe") do set "DISCPROBE=%%~fI"
echo.
if exist "%DISCPROBE%\discprobe\__main__.py" (
    echo [ok] disc-probe found at %DISCPROBE%
) else (
    where git >nul 2>&1
    if errorlevel 1 (
        echo [!] disc-probe is missing and Git is not installed.
        echo     Either install Git from https://git-scm.com/download/win and run setup.bat again,
        echo     or download https://github.com/Ashley-QwQ/disc-probe as a ZIP and extract it to:
        echo     %DISCPROBE%
        exit /b 1
    )
    echo Cloning disc-probe to %DISCPROBE% ...
    git clone https://github.com/Ashley-QwQ/disc-probe "%DISCPROBE%"
    if errorlevel 1 (
        echo [!] git clone failed. Check your internet connection and run setup.bat again.
        exit /b 1
    )
    echo [ok] disc-probe cloned.
)

rem 4. disc-probe self-test (no disc or drive needed)
echo.
echo Running disc-probe selftest...
pushd "%DISCPROBE%"
python -m discprobe selftest
set "SELFTEST=%errorlevel%"
popd
if not "%SELFTEST%"=="0" (
    echo [!] disc-probe's selftest reported a failed check. The output above names it.
    echo     DiscTuner still installs; if calibration fails, report the selftest line with your log.
)

rem 5. DiscImageStudio is a separate program; DiscTuner asks for its location if it is not found.
echo.
if exist "%LOCALAPPDATA%\Programs\DiscImageStudio\*.exe" (
    echo [ok] DiscImageStudio found in %LOCALAPPDATA%\Programs\DiscImageStudio
) else if exist "%USERPROFILE%\Downloads\DiscImageStudio*.exe" (
    echo [ok] DiscImageStudio found in Downloads
) else (
    echo [!] DiscImageStudio was not found. Download it from:
    echo     https://github.com/JiaFeiMiao-K-Cat/DiscImageStudio/releases/latest
    echo     DiscTuner will ask you where it is the first time you calibrate.
)

echo.
echo Setup finished. Start DiscTuner with run_disctuner.bat.
exit /b 0
