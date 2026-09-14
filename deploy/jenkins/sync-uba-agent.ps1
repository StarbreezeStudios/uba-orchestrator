$ErrorActionPreference = 'Stop'

$workspaceRoot = "D:\\jkws\\$env:COMPUTERNAME\\payday3\\trunk"
$p4Path = (Get-Command p4.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $workspaceRoot -PathType Container)) {
    throw "Perforce workspace root was not found: $workspaceRoot"
}

Push-Location $workspaceRoot
try {
    Write-Host "Syncing Perforce workspace to head: $workspaceRoot"
    & $p4Path sync
    if ($LASTEXITCODE -ne 0) {
        throw "Perforce sync failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
