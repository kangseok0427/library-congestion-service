$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo
& .\.venv\Scripts\python.exe -m pip install -r requirements-windows-uploader.txt
if ($LASTEXITCODE -ne 0) { throw 'Windows uploader dependencies failed to install' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name YongsanLibraryUploader --collect-submodules keyring.backends --hidden-import keyring.backends.Windows --paths $repo --specpath build scripts\uploader_entry.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
