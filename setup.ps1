$ErrorActionPreference = "Stop"
python --version
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
}
python evaluation\generate_dataset.py
python scripts\load_demo.py
Write-Host "Setup complete. Start the app with: .\run.ps1"
