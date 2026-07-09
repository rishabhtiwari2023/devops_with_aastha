$ErrorActionPreference = 'Stop'

if (-not (Test-Path -Path '.venv\Scripts\Activate.ps1')) {
    Write-Host 'Virtual environment not found. Create it with `python -m venv .venv`.' -ForegroundColor Yellow
    exit 1
}

& .\.venv\Scripts\Activate.ps1
python main.py
