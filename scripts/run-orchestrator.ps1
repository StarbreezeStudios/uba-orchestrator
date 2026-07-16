param(
    [int]$Port = 8080
)

$env:PYTHONPATH = "$PSScriptRoot\..\orchestrator"
python -m uvicorn app.main:app --host 0.0.0.0 --port $Port
