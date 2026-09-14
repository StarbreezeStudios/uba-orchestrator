$ErrorActionPreference = 'Stop'

$workspaceRoot = "D:\\jkws\\$env:COMPUTERNAME\\payday3\\trunk"
$buildScript = Join-Path $workspaceRoot 'Engine\\Build\\BatchFiles\\Build.bat'
$binaryDirectory = Join-Path $workspaceRoot 'Engine\\Binaries\\Win64\\UnrealBuildAccelerator\\x64'
$ubaAgent = Join-Path $binaryDirectory 'UbaAgent.exe'

if (-not (Test-Path -LiteralPath $buildScript -PathType Leaf)) {
    throw "Unreal Build.bat was not found: $buildScript"
}

Write-Host "Building UbaAgent from $workspaceRoot"
& $buildScript UbaAgent Win64 Development -WaitMutex -NoHotReload
if ($LASTEXITCODE -ne 0) {
    throw "UbaAgent build failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $ubaAgent -PathType Leaf)) {
    throw "UbaAgent.exe was not produced: $ubaAgent"
}

Write-Host "Built UbaAgent: $ubaAgent"
