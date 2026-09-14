pipeline {
    agent { label 'uba-helper' }

    environment {
        P4CLIENT = p4util.fetchClientName(env.NODE_NAME, jobContext.branchId)
    }

    options {
        timestamps()
        buildDiscarder(logRotator(daysToKeepStr: '365'))
        disableConcurrentBuilds()
    }

    stages {
        stage('Sync Perforce') {
            steps {
                powershell script: '& "$env:WORKSPACE\\deploy\\jenkins\\sync-uba-agent.ps1"'
            }
        }

        stage('Build UBA agent') {
            steps {
                powershell script: '& "$env:WORKSPACE\\deploy\\jenkins\\build-uba-agent.ps1"'
            }
        }

        stage('Publish UBA agent') {
            steps {
                powershell script: '& "$env:WORKSPACE\\deploy\\jenkins\\publish-uba-agent.ps1"'
            }
        }
    }
}
