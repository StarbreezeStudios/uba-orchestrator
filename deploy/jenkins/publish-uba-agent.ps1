$ErrorActionPreference = 'Stop'

$workspaceRoot = "D:\\jkws\\$env:COMPUTERNAME\\payday3\\trunk"
$binaryDirectory = Join-Path $workspaceRoot 'Engine\\Binaries\\Win64\\UnrealBuildAccelerator\\x64'
$artifactRoot = '\\devopsfs.starbreeze.com\\devops\\Software-Installs\\UnrealBuildAccelerator\\UbaAgent'
$artifactDirectory = Join-Path $artifactRoot "build-$env:BUILD_NUMBER"

if (-not (Test-Path -LiteralPath $artifactRoot -PathType Container)) {
    throw "UBA artifact root is not available: $artifactRoot"
}
if (Test-Path -LiteralPath $artifactDirectory) {
    throw "Refusing to overwrite existing UBA artifact: $artifactDirectory"
}

New-Item -ItemType Directory -Path $artifactDirectory -ErrorAction Stop | Out-Null
try {
    $files = @(
        Get-Item -LiteralPath (Join-Path $binaryDirectory 'UbaAgent.exe') -ErrorAction Stop
        Get-ChildItem -LiteralPath $binaryDirectory -Filter '*.dll' -File
    )

    foreach ($file in $files) {
        Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $artifactDirectory $file.Name) -ErrorAction Stop
    }

    $manifest = [ordered]@{
        build_number = [int]$env:BUILD_NUMBER
        source_workspace = $workspaceRoot
        files = @(
            Get-ChildItem -LiteralPath $artifactDirectory -File |
                Sort-Object Name |
                ForEach-Object {
                    $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
                    [ordered]@{
                        name = $_.Name
                        sha256 = $hash.Hash
                        size_bytes = $_.Length
                    }
                }
        )
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $artifactDirectory 'manifest.json') -Encoding utf8
}
catch {
    Remove-Item -LiteralPath $artifactDirectory -Recurse -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "Published UBA agent artifact: $artifactDirectory"
Get-ChildItem -LiteralPath $artifactDirectory -File | Select-Object Name, Length | Format-Table -AutoSize | Out-String | Write-Host
