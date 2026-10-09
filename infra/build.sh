#!/bin/sh
# Explicit shared images, never task translation or container lifecycle.
set -eu
cd "$(dirname "$0")/.."
docker build -f infra/agent.Dockerfile -t harbor-native-agent:spike .
docker build -f infra/runtime.Dockerfile -t harbor-native-runtime:spike .
