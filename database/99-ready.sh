#!/bin/sh
set -eu
# Docker runs this after every SQL seed file, so health cannot pass between
# schema/knowledge setup and the synthetic fixture load.
touch "$PGDATA/.hutch_initialized"
