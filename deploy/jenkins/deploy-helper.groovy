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

        stage('Stop existing helper') {
            steps {
                powershell '''
                    $ErrorActionPreference = 'Stop'
                    $task = Get-ScheduledTask -TaskName 'UbaOrchestratorHelper' -ErrorAction SilentlyContinue
                    if ($task) {
                        Stop-ScheduledTask -InputObject $task
                    }

                    $helpers = Get-CimInstance Win32_Process | Where-Object {
                        $_.Name -in @('python.exe', 'pythonw.exe') -and
                        $_.CommandLine -like '*UbaOrchestrator\\helper-agent\\agent.py*'
                    }
                    $helpers | Select-Object ProcessId, ExecutablePath, CommandLine
                    $helpers | ForEach-Object {
                        Stop-Process -Id $_.ProcessId -Force
                    }
                '''
            }
        }

        stage('Install helper') {
            steps {
                python 'deploy/jenkins/deploy-helper.py'
            }
        }
    }
}
