pipeline {
    agent any

    parameters {
        string(name: 'DEPLOY_HOST', defaultValue: 'helsinki')
        string(name: 'DEPLOY_DIR', defaultValue: '/opt/uba-orchestrator')
        string(name: 'GIT_URL', defaultValue: '')
        string(name: 'GIT_REF', defaultValue: 'master')
        string(name: 'SSH_CREDENTIALS_ID', defaultValue: 'helsinki-uba-deploy-key')
    }

    stages {
        stage('Deploy orchestrator') {
            steps {
                script {
                    if (!params.GIT_URL?.trim()) {
                        error('GIT_URL must be configured for the deployment')
                    }

                    sshagent(credentials: [params.SSH_CREDENTIALS_ID]) {
                        withEnv([
                            "DEPLOY_HOST=${params.DEPLOY_HOST}",
                            "DEPLOY_DIR=${params.DEPLOY_DIR}",
                            "GIT_URL=${params.GIT_URL}",
                            "GIT_REF=${params.GIT_REF}"
                        ]) {
                            sh '''#!/bin/sh -eu
set -x
ssh -o BatchMode=yes "$DEPLOY_HOST" "DEPLOY_DIR='$DEPLOY_DIR' GIT_URL='$GIT_URL' GIT_REF='$GIT_REF' bash -s" <<'REMOTE'
set -eu
if [ -d "$DEPLOY_DIR/.git" ]; then
    git -C "$DEPLOY_DIR" fetch --prune origin "$GIT_REF"
    git -C "$DEPLOY_DIR" checkout --force "$GIT_REF"
    git -C "$DEPLOY_DIR" reset --hard "origin/$GIT_REF"
else
    mkdir -p "$(dirname "$DEPLOY_DIR")"
    git clone --branch "$GIT_REF" "$GIT_URL" "$DEPLOY_DIR"
fi
cd "$DEPLOY_DIR"
docker compose -f deploy/docker/compose.yaml up -d --build --remove-orphans
docker compose -f deploy/docker/compose.yaml ps
REMOTE
'''
                        }
                    }
                }
            }
        }
    }
}
