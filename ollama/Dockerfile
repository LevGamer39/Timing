FROM ollama/ollama:latest

# Модель запекается в образ, чтобы на чистой машине не качать её заново.
# Смена модели — только пересборкой образа (аргумент сборки).
ARG OLLAMA_MODEL=qwen2.5:7b
RUN ollama serve & sleep 5 && ollama pull "$OLLAMA_MODEL" && sleep 2 && pkill ollama || true

ENV OLLAMA_KEEP_ALIVE=-1
