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
                sh '''
                    for attempt in $(seq 1 30); do
                        if curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/health; then
                            exit 0
                        fi
                        sleep 2
                    done
                    echo 'Orchestrator did not become healthy within 60 seconds' >&2
                    docker compose -f deploy/docker/compose.yaml logs --tail=100 uba-orchestrator
                    exit 1
                '''
            }
        }
    }
}
