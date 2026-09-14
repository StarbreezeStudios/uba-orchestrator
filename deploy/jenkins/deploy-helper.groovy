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
                powershell script: '& "$env:WORKSPACE\\deploy\\jenkins\\deploy-helper.ps1"'
            }
        }
    }
}
