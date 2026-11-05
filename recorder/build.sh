#!/bin/sh
# Builds the ScribeIQ recorder helper into recorder/bin/scribe-recorder.
# Requires the Xcode command line tools (xcode-select --install).
set -e
cd "$(dirname "$0")"
mkdir -p bin
swiftc -O ScribeRecorder.swift -o bin/scribe-recorder \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker Info.plist
echo "Built $(pwd)/bin/scribe-recorder"
