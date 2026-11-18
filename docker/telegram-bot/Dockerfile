FROM node:18-bookworm-slim

WORKDIR /app

ENV NODE_ENV=production
ENV TZ=America/Santo_Domingo

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev

COPY config.js bot.js ./
COPY lib ./lib
COPY modules ./modules

RUN mkdir -p /app/data /app/ssh

CMD ["node", "--dns-result-order=ipv4first", "bot.js"]
