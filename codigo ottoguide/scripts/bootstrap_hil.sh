set -euo pipefail

sudo apt-get update
sudo apt-get install -y curl docker.io docker-compose
sudo systemctl enable --now docker

curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b

until curl -sf http://127.0.0.1:11434/api/tags | grep -q '"name":"qwen2.5:3b"'; do
  sleep 2
done

docker-compose up -d --build
