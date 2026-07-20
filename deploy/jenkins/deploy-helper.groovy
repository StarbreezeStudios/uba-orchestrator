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

function Get-UbaHelperProcesses {
    @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and (
            $_.CommandLine -like '*helper-agent*agent.py*' -or
            ($_.Name -ieq 'UbaAgent.exe' -and $_.CommandLine -match "-listen=$listenPort")
        )
    })
}

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.State -eq 'Running') {
    Write-Host "Stopping existing scheduled task $taskName"
    $schtasks = Join-Path (Join-Path $env:SystemRoot 'System32') 'schtasks.exe'
    $endTaskProcess = Start-Process `
        -FilePath $schtasks `
        -ArgumentList @('/End', '/TN', $taskName) `
        -NoNewWindow `
        -PassThru

    if (-not $endTaskProcess.WaitForExit(10000)) {
        Stop-Process -Id $endTaskProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Warning "Timed out while asking Task Scheduler to stop $taskName"
    }
    else {
        $endTaskProcess.WaitForExit()
        $endTaskProcess.Refresh()
        if ($endTaskProcess.ExitCode -ne 0) {
            Write-Warning "Task Scheduler returned exit code $($endTaskProcess.ExitCode) while stopping $taskName"
        }
    }
}

Get-UbaHelperProcesses |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$stopDeadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 250
    $remainingHelperProcesses = Get-UbaHelperProcesses
} while ($remainingHelperProcesses.Count -gt 0 -and (Get-Date) -lt $stopDeadline)

if ($remainingHelperProcesses.Count -gt 0) {
    $processIds = $remainingHelperProcesses.ProcessId -join ', '
    throw "Could not stop existing UBA helper processes: $processIds"
}

$taskStopDeadline = (Get-Date).AddSeconds(30)
do {
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $existingTask -or $existingTask.State -ne 'Running') {
        break
    }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $taskStopDeadline)

if ($existingTask -and $existingTask.State -eq 'Running') {
    throw "Scheduled task $taskName is still running after its helper processes were stopped"
}

New-Item -ItemType Directory -Path (Split-Path $agentScript), $logDir -Force | Out-Null
Copy-Item -LiteralPath $sourceScript -Destination $agentScript -Force

New-NetFirewallRule -DisplayName 'UBA Orchestrator Helper 1346' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $listenPort `
    -Profile Domain,Private -ErrorAction SilentlyContinue | Out-Null

$arguments = "-u `"$agentScript`" --orchestrator $orchestratorUrl --uba-agent `"$ubaAgent`" --address $address --listen-port $listenPort --log-dir `"$logDir`""
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $installRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'jkoperator'
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId 'jkoperator' -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
$helperStartRequestedAtUtc = [DateTimeOffset]::UtcNow
Start-ScheduledTask -TaskName $taskName

$supervisorDeadline = (Get-Date).AddSeconds(15)
do {
    Start-Sleep -Milliseconds 500
    $supervisorProcesses = @(Get-UbaHelperProcesses | Where-Object {
        $_.CommandLine -like '*helper-agent*agent.py*'
    })
} while ($supervisorProcesses.Count -eq 0 -and (Get-Date) -lt $supervisorDeadline)

if ($supervisorProcesses.Count -eq 0) {
    $taskState = (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    throw "Scheduled task $taskName did not start the helper supervisor (state: $taskState, last result: $($taskInfo.LastTaskResult))"
}

Write-Host "Helper supervisor started with process ID(s): $($supervisorProcesses.ProcessId -join ', ')"

$registrationTimeoutSeconds = 180
$deadline = (Get-Date).AddSeconds($registrationTimeoutSeconds)
$registeredHelper = $null
do {
    Start-Sleep -Seconds 2
    try {
        $registeredHelper = @(Invoke-RestMethod "$orchestratorUrl/api/v1/helpers") |
            Where-Object {
                $hostnameMatches = [string]$_.hostname -ieq [string]$env:COMPUTERNAME
                $heartbeatIsFresh = $false
                if ($hostnameMatches) {
                    try {
                        $heartbeatIsFresh = [DateTimeOffset]::Parse([string]$_.last_seen) -ge $helperStartRequestedAtUtc
                    }
                    catch {
                        $heartbeatIsFresh = $false
                    }
                }
                $hostnameMatches -and $heartbeatIsFresh
            } |
            Sort-Object last_seen -Descending |
            Select-Object -First 1
    }
    catch {
        $registeredHelper = $null
    }
} while (-not $registeredHelper -and (Get-Date) -lt $deadline)

if (-not $registeredHelper) {
    throw "Helper did not register with $orchestratorUrl within $registrationTimeoutSeconds seconds"
}

Write-Host "Registered helper address: $($registeredHelper.address):$($registeredHelper.listen_port)"
$registeredHelper | ConvertTo-Json -Depth 5 | Write-Host
Write-Host "UBA helper deployment completed"
'''
                    }
                }
            }
        }
    }
}
