[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EngineRoot
)

$ErrorActionPreference = "Stop"

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$diffPath = Join-Path $patchRoot "UBAExecutor.diff"
$actionGraphDiffPath = Join-Path $patchRoot "ActionGraph.diff"
$sourcePath = Join-Path $patchRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAAgentCoordinatorOrchestrator.cs"
$targetPath = Join-Path $EngineRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAAgentCoordinatorOrchestrator.cs"
$executorPath = Join-Path $EngineRoot "Engine\Source\Programs\UnrealBuildTool\Executors\UnrealBuildAccelerator\UBAExecutor.cs"
$actionGraphPath = Join-Path $EngineRoot "Engine\Source\Programs\UnrealBuildTool\System\ActionGraph.cs"

foreach ($path in @($diffPath, $actionGraphDiffPath, $sourcePath, $executorPath, $actionGraphPath)) {
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

$actionGraphDiffContent = Get-Content -LiteralPath $actionGraphDiffPath -Raw
if ($actionGraphDiffContent -notmatch "Forcing UBA executor for orchestrator build") {
    throw "ActionGraph.diff does not contain the expected orchestrator integration"
}

$existingExecutorContent = Get-Content -LiteralPath $executorPath -Raw
if ($existingExecutorContent -match 'UBAAgentCoordinatorOrchestrator') {
    Write-Host "UBA UBT patch is already applied"
}

else {
    Push-Location $EngineRoot
    try {
        $applyCheckOutput = & git apply --check --unsafe-paths $diffPath 2>&1
        $applyCheckExitCode = $LASTEXITCODE
        if ($applyCheckExitCode -eq 0) {
            $applyOutput = & git apply --unsafe-paths $diffPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "UBAExecutor.diff failed to apply"
            }
        }
        else {
            Write-Host "UBAExecutor.diff does not match this UBT revision; applying the orchestrator block by markers"

            $executorContent = Get-Content -LiteralPath $executorPath -Raw
            $startMarker = "List<UBAAgentCoordinatorHorde> hordeAgentCoordinators = new();"
            $endMarker = "_agentCoordinators.AddRange(hordeAgentCoordinators.DistinctBy(x => x.Server));"
            $startIndex = $executorContent.IndexOf($startMarker)
            $endIndex = -1
            if ($startIndex -ge 0) {
                $endIndex = $executorContent.IndexOf($endMarker, $startIndex)
            }

            if ($startIndex -lt 0 -or $endIndex -lt 0) {
                throw "UBAExecutor.cs does not contain the expected Horde coordinator block"
            }

            $replacement = @'
bool useOrchestrator = String.Equals(
        Environment.GetEnvironmentVariable("UBA_COORDINATOR"),
        "Orchestrator",
        StringComparison.OrdinalIgnoreCase);

List<UBAAgentCoordinatorHorde> hordeAgentCoordinators = new();
bool orchestratorAdded = false;

foreach (TargetDescriptor targetDescriptor in targetDescriptors)
{
        if (targetDescriptor.HotReloadMode != HotReloadMode.Disabled)
                UBAConfig.bStoreObjFilesCompressed = false;

        targetDescriptor.AdditionalArguments.ApplyTo(this);
        targetDescriptor.AdditionalArguments.ApplyTo(UBAConfig);

        if (useOrchestrator)
        {
                if (!orchestratorAdded)
                {
                        _agentCoordinators.Add(
                                new UBAAgentCoordinatorOrchestrator(
                                        logger,
                                        UBAConfig,
                                        targetDescriptor.AdditionalArguments,
                                        targetDescriptor.ProjectFile?.Directory));

                        orchestratorAdded = true;
                }
        }
        else
        {
                hordeAgentCoordinators.Add(
                        new UBAAgentCoordinatorHorde(
                                logger,
                                UBAConfig,
                                targetDescriptor.AdditionalArguments,
                                targetDescriptor.ProjectFile?.Directory));
        }
}

if (useOrchestrator)
{
        _remoteConnectionMode = "Direct";
}
else
{
        hordeAgentCoordinators.RemoveAll(x => !x.Enabled);
        _remoteConnectionMode =
                hordeAgentCoordinators.FirstOrDefault()?.ConnectionModeString ??
                "Local";

        _agentCoordinators.AddRange(
                hordeAgentCoordinators.DistinctBy(x => x.Server));
}
'@
            $replacement = $replacement.Trim()
            $endIndex += $endMarker.Length
            $executorContent = $executorContent.Substring(0, $startIndex) + $replacement + $executorContent.Substring($endIndex)
            Set-Content -LiteralPath $executorPath -Value $executorContent -Encoding UTF8
        }
    }
    finally {
        Pop-Location
    }
}

$existingActionGraphContent = Get-Content -LiteralPath $actionGraphPath -Raw
if ($existingActionGraphContent -match 'Forcing UBA executor for orchestrator build') {
    Write-Host "UBA ActionGraph patch is already applied"
}
else {
    Push-Location $EngineRoot
    try {
        $applyCheckOutput = & git apply --check --unsafe-paths $actionGraphDiffPath 2>&1
        $applyCheckExitCode = $LASTEXITCODE
        if ($applyCheckExitCode -eq 0) {
            $applyOutput = & git apply --unsafe-paths $actionGraphDiffPath 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "ActionGraph.diff failed to apply"
            }
        }
        else {
            Write-Host "ActionGraph.diff does not match this UBT revision; applying the orchestrator block by markers"

            $actionGraphContent = Get-Content -LiteralPath $actionGraphPath -Raw
            $insertionMarker = "int MinActionsForRemote = ParallelExecutor.GetDefaultNumParallelProcesses(BuildConfiguration.MaxParallelActions, BuildConfiguration.bAllCores, Logger);"
            $insertionIndex = $actionGraphContent.IndexOf($insertionMarker)
            if ($insertionIndex -lt 0) {
                throw "ActionGraph.cs does not contain the expected executor selection marker"
            }

            $replacement = @'


                        bool useOrchestrator = String.Equals(
                                Environment.GetEnvironmentVariable("UBA_COORDINATOR"),
                                "Orchestrator",
                                StringComparison.OrdinalIgnoreCase);
                        if (useOrchestrator)
                        {
                                ActionExecutor? ubaExecutor = GetRemoteExecutorByName(
                                        "UBA",
                                        BuildConfiguration,
                                        ActionCount,
                                        MinActionsForRemote,
                                        TargetDescriptors,
                                        Logger);
                                if (ubaExecutor != null)
                                {
                                        Logger.LogInformation("Forcing UBA executor for orchestrator build");
                                        return ubaExecutor;
                                }
                        }
'@
            $insertionIndex += $insertionMarker.Length
            $actionGraphContent = $actionGraphContent.Substring(0, $insertionIndex) + $replacement.TrimEnd() + $actionGraphContent.Substring($insertionIndex)
            Set-Content -LiteralPath $actionGraphPath -Value $actionGraphContent -Encoding UTF8
        }
    }
    finally {
        Pop-Location
    }
}

$targetDirectory = Split-Path -Parent $targetPath
New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force

$appliedContent = Get-Content -LiteralPath $executorPath -Raw
if ($appliedContent -notmatch 'UBAAgentCoordinatorOrchestrator') {
    throw "UBAExecutor.cs does not contain the orchestrator integration after patching"
}

$appliedActionGraphContent = Get-Content -LiteralPath $actionGraphPath -Raw
if ($appliedActionGraphContent -notmatch 'Forcing UBA executor for orchestrator build') {
    throw "ActionGraph.cs does not contain the orchestrator executor selection after patching"
}

Write-Host "UBA UBT patch applied successfully to $EngineRoot"
