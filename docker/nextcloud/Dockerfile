# Nextcloud + ffmpeg: miniaturas y transcodificación de vídeo para Memories
FROM nextcloud:latest
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
