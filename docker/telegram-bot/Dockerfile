FROM node:18-bookworm-slim

WORKDIR /app

ENV NODE_ENV=production
ENV TZ=America/Santo_Domingo

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev

COPY ai_brain ./ai_brain
RUN pip3 install --no-cache-dir --break-system-packages google-genai httpx

COPY config.js bot.js start.sh ./
COPY lib ./lib
COPY modules ./modules

RUN mkdir -p /app/data /app/ssh && chmod +x /app/start.sh

ENV AI_BRAIN_PORT=5057
ENV PYTHONUNBUFFERED=1

CMD ["./start.sh"]
