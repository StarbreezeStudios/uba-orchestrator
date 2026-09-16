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
                python 'deploy/jenkins/sync-uba-agent.py'
            }
        }

        stage('Build UBA agent') {
            steps {
                python 'deploy/jenkins/build-uba-agent.py'
            }
        }

        stage('Publish UBA agent') {
            steps {
                python 'deploy/jenkins/publish-uba-agent.py'
            }
        }
    }
}
