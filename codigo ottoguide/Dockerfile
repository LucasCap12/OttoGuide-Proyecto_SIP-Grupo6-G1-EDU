FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY api ./api
COPY config ./config
COPY hardware ./hardware
COPY src ./src
COPY main.py ./main.py

RUN python -m pip install --upgrade pip && pip install .

ENV ROBOT_MODE=mock

CMD ["python", "main.py"]
