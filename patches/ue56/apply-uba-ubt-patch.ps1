[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EngineRoot
)

$ErrorActionPreference = "Stop"

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$diffPath = Join-Path $patchRoot "UBAExecutor.diff"
$sourcePath = Join-Path $patchRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAAgentCoordinatorOrchestrator.cs"
$targetPath = Join-Path $EngineRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAAgentCoordinatorOrchestrator.cs"
$executorPath = Join-Path $EngineRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAExecutor.cs"

foreach ($path in @($diffPath, $sourcePath, $executorPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required patch file is missing: $path"
    }
}

if (-not (Test-Path -LiteralPath $EngineRoot -PathType Container)) {
    throw "Engine root does not exist: $EngineRoot"
}

$diffContent = Get-Content -LiteralPath $diffPath -Raw
if ($diffContent -notmatch "UBA_COORDINATOR") {
    throw "UBAExecutor.diff does not contain the expected orchestrator integration"
}

Push-Location $EngineRoot
try {
    & git apply --check --unsafe-paths $diffPath
    if ($LASTEXITCODE -ne 0) {
        throw "UBAExecutor.diff cannot be applied cleanly"
    }

    & git apply --unsafe-paths $diffPath
    if ($LASTEXITCODE -ne 0) {
        throw "UBAExecutor.diff failed to apply"
    }
}
finally {
    Pop-Location
}

$targetDirectory = Split-Path -Parent $targetPath
New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force

$appliedContent = Get-Content -LiteralPath $executorPath -Raw
if ($appliedContent -notmatch 'UBAAgentCoordinatorOrchestrator') {
    throw "UBAExecutor.cs does not contain the orchestrator integration after patching"
}

Write-Host "UBA UBT patch applied successfully to $EngineRoot"
