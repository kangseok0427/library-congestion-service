$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name YongsanLibraryUploader --collect-submodules keyring.backends --hidden-import keyring.backends.Windows --paths $repo --specpath build scripts\uploader_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
