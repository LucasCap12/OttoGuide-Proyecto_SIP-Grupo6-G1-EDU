#!/usr/bin/env bash
set -euo pipefail

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux no esta instalado"
  echo "Instalar con: sudo apt-get update && sudo apt-get install -y tmux"
  exit 1
fi

SESSION_NAME="ottoguide_sitl"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MUJOCO_DIR="${ROOT_DIR}/libs/unitree_mujoco-main"
ROS2_DIR="${ROOT_DIR}/libs/unitree_ros2-master"
ISAAC_DIR="${ROOT_DIR}/libs/unitree_sim_isaaclab-main"

if tmux has-session -t "${SESSION_NAME}" 2>/dev/null; then
  tmux kill-session -t "${SESSION_NAME}"
fi

tmux new-session -d -s "${SESSION_NAME}" -n sitl

tmux split-window -h -t "${SESSION_NAME}:0"
tmux split-window -v -t "${SESSION_NAME}:0.0"
tmux split-window -v -t "${SESSION_NAME}:0.1"

tmux send-keys -t "${SESSION_NAME}:0.0" "cd '${MUJOCO_DIR}' && python3 simulate.py g1/scene_29dof.xml" C-m
tmux send-keys -t "${SESSION_NAME}:0.1" "source /opt/ros/humble/setup.bash && cd '${ROS2_DIR}' && ros2 launch unitree_ros2 g1_sitl_bridge.launch.py" C-m
tmux send-keys -t "${SESSION_NAME}:0.2" "cd '${ISAAC_DIR}' && python3 sim_main.py --usd '${ROOT_DIR}/libs/unitree_sim_isaaclab-main/robots/g1/g1.usd'" C-m
tmux send-keys -t "${SESSION_NAME}:0.3" "cd '${ROOT_DIR}' && export ROBOT_MODE=sim && uvicorn main:app --host 0.0.0.0 --port 8000" C-m

tmux select-layout -t "${SESSION_NAME}:0" tiled

echo "Sesion tmux creada: ${SESSION_NAME}"
echo "Adjuntar con: tmux attach -t ${SESSION_NAME}"
