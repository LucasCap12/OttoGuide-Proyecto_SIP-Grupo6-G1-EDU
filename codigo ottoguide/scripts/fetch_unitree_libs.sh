set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LIBS_DIR="${PROJECT_ROOT}/libs"

SDK2_URL="https://github.com/unitreerobotics/unitree_sdk2_python.git"
MUJOCO_URL="https://github.com/unitreerobotics/unitree_mujoco.git"
ROS2_URL="https://github.com/unitreerobotics/unitree_ros2.git"

mkdir -p "${LIBS_DIR}"

clone_or_update() {
  local repo_url="$1"
  local target_dir="$2"

  if [ -d "${target_dir}/.git" ]; then
    git -C "${target_dir}" fetch --depth 1 origin
    git -C "${target_dir}" reset --hard origin/HEAD
  else
    git clone --depth 1 "${repo_url}" "${target_dir}"
  fi
}

clone_or_update "${SDK2_URL}" "${LIBS_DIR}/unitree_sdk2_python"
clone_or_update "${MUJOCO_URL}" "${LIBS_DIR}/unitree_mujoco"
clone_or_update "${ROS2_URL}" "${LIBS_DIR}/unitree_ros2"

if command -v chmod >/dev/null 2>&1; then
  chmod +x "${SCRIPT_DIR}/fetch_unitree_libs.sh" || true
fi

echo "Dependencias Unitree provisionadas en: ${LIBS_DIR}"
