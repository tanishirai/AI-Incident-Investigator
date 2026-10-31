$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv\Scripts\Activate.ps1")) {
    Write-Error "Environment missing. Run .\setup.ps1 first."
}
& .\.venv\Scripts\Activate.ps1
streamlit run app.py
