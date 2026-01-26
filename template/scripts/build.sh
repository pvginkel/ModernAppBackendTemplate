#!/bin/sh

set -e

. "$(dirname "$0")/stop.sh"

docker build -t "$NAME":latest .
