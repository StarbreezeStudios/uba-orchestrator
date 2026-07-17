pipeline {
    agent { label "${params.node_name}-service" }

    parameters {
        string(
            name: 'node_name',
            defaultValue: '',
            description: 'Windows Jenkins node that will run the UBA helper'
        )
    }

    options {
        timestamps()
        buildDiscarder(logRotator(daysToKeepStr: '365'))
        disableConcurrentBuilds()
    }

    stages {
        stage('Set Job name') {
            steps {
                script {
                    currentBuild.displayName += " ${params.node_name}"
                }
            }
        }

        stage('Install helper') {
            steps {
                script {
                    withEnv(["UBA_TARGET_NODE=${params.node_name}"]) {
                        powershell '''
$ErrorActionPreference = 'Stop'

$installRoot = 'C:\\ProgramData\\Epic\\UbaOrchestrator'
$agentScript = Join-Path $installRoot 'helper-agent\\agent.py'
$logDir = Join-Path $installRoot 'logs\\helper'
$taskName = 'UbaOrchestratorHelper'
$orchestratorUrl = 'http://helsinki:8080'
$listenPort = 1346
$ubaAgent = "D:\\jkws\\$env:COMPUTERNAME\\payday3\\trunk\\Engine\\Binaries\\Win64\\UnrealBuildAccelerator\\x64\\UbaAgent.exe"
$sourceScript = Join-Path $env:WORKSPACE 'helper-agent\\agent.py'

if (-not (Test-Path -LiteralPath $sourceScript -PathType Leaf)) {
    throw "Helper agent source was not found: $sourceScript"
}
if (-not (Test-Path -LiteralPath $ubaAgent -PathType Leaf)) {
    throw "UbaAgent.exe was not found: $ubaAgent"
}

$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' |
    Sort-Object RouteMetric, InterfaceMetric |
    Select-Object -First 1
$address = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex |
    Where-Object { $_.IPAddress -ne '127.0.0.1' } |
    Select-Object -First 1).IPAddress
if (-not $address) {
    throw 'Could not determine the helper IPv4 address'
}

Write-Host "Installing UBA helper on $env:COMPUTERNAME ($address)"
Write-Host "Using UBA agent $ubaAgent"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'helper-agent[\\/]agent.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Path (Split-Path $agentScript), $logDir -Force | Out-Null
Copy-Item -LiteralPath $sourceScript -Destination $agentScript -Force

New-NetFirewallRule -DisplayName 'UBA Orchestrator Helper 1346' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $listenPort `
    -Profile Domain,Private -ErrorAction SilentlyContinue | Out-Null

$arguments = "-u `"$agentScript`" --orchestrator $orchestratorUrl --uba-agent `"$ubaAgent`" --address $address --listen-port $listenPort --log-dir `"$logDir`""
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $installRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'jkoperator'
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId 'jkoperator' -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$deadline = (Get-Date).AddSeconds(30)
$registeredHelper = $null
do {
    Start-Sleep -Seconds 2
    try {
        $registeredHelper = Invoke-RestMethod "$orchestratorUrl/api/v1/helpers" |
            Where-Object { $_.hostname -eq $env:COMPUTERNAME -and $_.address -eq $address } |
            Select-Object -First 1
    }
    catch {
        $registeredHelper = $null
    }
} while (-not $registeredHelper -and (Get-Date) -lt $deadline)

if (-not $registeredHelper) {
    throw "Helper did not register with $orchestratorUrl within 30 seconds"
}

$registeredHelper | ConvertTo-Json -Depth 5 | Write-Host
Write-Host "UBA helper deployment completed"
'''
                    }
                }
            }
        }
    }
}
