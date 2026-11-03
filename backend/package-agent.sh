#!/bin/bash
# package-agent.sh — build the agent for every platform and assemble a complete
# USB-ready install folder at dist/cryptalas-agent/ (+ a .zip).
set -u
cd "$(dirname "$0")" || exit 1
./build-agent.sh all >/dev/null || exit 1

D=dist/cryptalas-agent
rm -rf "$D"
mkdir -p "$D/gpu"
cp bin/cryptalas              "$D/cryptalas"
cp bin/cryptalas-linux-amd64  "$D/cryptalas-linux-amd64"
cp bin/cryptalas.exe          "$D/cryptalas.exe"
cp gpu/cuda_train.py          "$D/gpu/cuda_train.py"
cp gpu/forecast.py            "$D/gpu/forecast.py"
cp gpu/optimize.py            "$D/gpu/optimize.py"
cp dist-templates/install.sh     "$D/install.sh"
cp dist-templates/install.bat    "$D/install.bat"
cp dist-templates/README.txt     "$D/README.txt"
cp dist-templates/supervise.bat  "$D/supervise.bat"
cp supervise-cryptalas.sh        "$D/supervise-cryptalas.sh"
chmod +x "$D/supervise-cryptalas.sh"
chmod +x "$D/install.sh"

(cd dist && zip -r cryptalas-agent.zip cryptalas-agent/ >/dev/null)
echo "package ready:"
echo "  dist/cryptalas-agent/      (the folder)"
echo "  dist/cryptalas-agent.zip   (the archive)"
