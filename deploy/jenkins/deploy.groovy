pipeline {
    agent { label 'helsinki' }

    options {
        timestamps()
        buildDiscarder(logRotator(daysToKeepStr: '365'))
        disableConcurrentBuilds()
    }

    stages {
        stage('Deploy orchestrator') {
            steps {
                sh 'UID=$(id -u) docker compose -f deploy/docker/compose.yaml up -d --build --force-recreate --remove-orphans'
                sh 'docker compose -f deploy/docker/compose.yaml ps'
                sh 'curl --fail --silent http://127.0.0.1:8080/api/v1/health'
            }
        }
    }
}
