#!/usr/bin/env bash
set -euo pipefail

MAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PGM_PATH="${MAP_DIR}/uade_3d_sim.pgm"
YAML_PATH="${MAP_DIR}/uade_3d_sim.yaml"

printf "P2\n1 1\n255\n254\n" > "${PGM_PATH}"

cat > "${YAML_PATH}" <<EOF
image: uade_3d_sim.pgm
mode: trinary
resolution: 0.05
origin: [0.0, 0.0, 0.0]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
EOF

echo "Mapa base generado en ${MAP_DIR}"
