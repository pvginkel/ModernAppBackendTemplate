#!/usr/bin/env bash

while true; do
    $SSE_GATEWAY_ROOT/scripts/run-gateway.sh --callback-url http://localhost:5000/api/sse/callback --port 3001

    echo
    echo "Press any key to restart the server..."
    echo

    read -n1 -s
done
