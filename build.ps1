# Builds dist\TrikiAudio\TrikiAudio.exe (+ a zip for GitHub releases).
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade -r requirements.txt "pyinstaller==6.11.1"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
# working on the library next door? build with that copy instead of the GitHub one
if (Test-Path "../triki-py/pyproject.toml") {
    python -m pip install -e "../triki-py[windows]"
    if ($LASTEXITCODE -ne 0) { throw "installing ../triki-py failed" }
}

# app icon (the same gold cap the app draws for its window and tray)
python -c "from tray import cap_image; cap_image(256).save('build_icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(256,256)])"

python -m PyInstaller --noconfirm --clean --windowed --name TrikiAudio --icon build_icon.ico `
    --collect-data customtkinter `
    --collect-submodules winrt `
    --collect-submodules triki `
    --hidden-import pystray._win32 `
    --hidden-import comtypes.stream `
    triki_audio.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# smoke test: start the packaged app headless in demo mode and check it ran
$result = Join-Path $env:TEMP "trikiaudio_selftest.json"
Remove-Item $result -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "dist\TrikiAudio\TrikiAudio.exe" -ArgumentList "--selftest", "`"$result`"" -PassThru -Wait
Get-Content $result
if ($p.ExitCode -ne 0) { throw "self-test of the built exe failed" }

$zip = "dist\TrikiAudio-windows.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "dist\TrikiAudio" -DestinationPath $zip
Write-Host "Built $zip"
