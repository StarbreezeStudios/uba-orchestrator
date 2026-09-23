pipeline {
    agent { label "${params.node_name}" }

    parameters {
        choice(
            name: 'node_name',
            choices: ['uba-orchestrator-dev', 'uba-orchestrator'],
            description: 'Target node name'
        )
    }

    options {
        timestamps()
        buildDiscarder(logRotator(daysToKeepStr: '365'))
        disableConcurrentBuilds()
    }

    environment {
        PUBLIC_DOMAIN = 'helsinki.starbreeze.com'
        TLS_DIRECTORY = '/home/jkoperator/jenkins-tls/uba-orchestrator'
    }

    stages {
        stage('Validate deployment configuration') {
            steps {
                sh 'docker compose -f deploy/docker/compose.yaml config --quiet'
            }
        }

        stage('Install TLS certificate') {
            steps {
                withCredentials([file(credentialsId: 'cdf92e2d-5e82-4075-a898-20a1ac3d3532', variable: 'STARBREEZE_TLS_CERTS_ARCHIVE')]) {
                    sh '''#!/usr/bin/env bash
                        set -euo pipefail
                        tls_staging_directory=$(mktemp -d)
                        chmod 0700 "$tls_staging_directory"
                        trap 'rm -rf "$tls_staging_directory"' EXIT

                        tar -xzf "$STARBREEZE_TLS_CERTS_ARCHIVE" \
                            --no-same-owner --no-same-permissions \
                            -C "$tls_staging_directory" \
                            starbreeze/starbreeze.com.pem starbreeze/starbreeze.com.key

                        certificate_file="$tls_staging_directory/starbreeze/starbreeze.com.pem"
                        key_file="$tls_staging_directory/starbreeze/starbreeze.com.key"
                        openssl x509 -in "$certificate_file" -noout -checkhost "$PUBLIC_DOMAIN"
                        openssl x509 -in "$certificate_file" -noout -checkend 0
                        openssl pkey -in "$key_file" -noout
                        certificate_public_key=$(openssl x509 -in "$certificate_file" -noout -pubkey | openssl pkey -pubin -outform DER | openssl sha256)
                        private_key_public_key=$(openssl pkey -in "$key_file" -pubout -outform DER | openssl sha256)
                        test "$certificate_public_key" = "$private_key_public_key"

                        install -d -m 0700 "$TLS_DIRECTORY"
                        install -m 0644 "$certificate_file" "$TLS_DIRECTORY/starbreeze.com.pem"
                        install -m 0600 "$key_file" "$TLS_DIRECTORY/starbreeze.com.key"
                    '''
                }
            }
        }

        stage('Deploy orchestrator') {
            steps {
                sh 'UID=$(id -u) docker compose -f deploy/docker/compose.yaml up -d --build --force-recreate --remove-orphans'
                sh 'docker compose -f deploy/docker/compose.yaml ps'
                sh '''
                    for attempt in $(seq 1 30); do
                        if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8080/api/v1/health &&
                           curl --fail --silent --show-error --max-time 5 --noproxy '*' \
                               --resolve "$PUBLIC_DOMAIN:443:127.0.0.1" "https://$PUBLIC_DOMAIN/ui" >/dev/null &&
                           curl --fail --silent --show-error --max-time 5 --noproxy '*' \
                               --resolve "$PUBLIC_DOMAIN:443:127.0.0.1" "https://$PUBLIC_DOMAIN/api/v1/health"; then
                            exit 0
                        fi
                        sleep 2
                    done
                    echo 'Backend and HTTPS checks did not pass after 30 attempts' >&2
                    docker compose -f deploy/docker/compose.yaml logs --tail=100 uba-orchestrator nginx
                    exit 1
                '''
            }
        }
    }
}
