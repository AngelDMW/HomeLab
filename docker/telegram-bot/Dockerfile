FROM node:18-bullseye-slim

WORKDIR /app

ENV NODE_ENV=production
ENV TZ=America/Santo_Domingo

COPY package.json ./
RUN npm install --omit=dev

COPY config.js bot.js ./
COPY lib ./lib
COPY modules ./modules

RUN mkdir -p /app/data

CMD ["node", "--dns-result-order=ipv4first", "bot.js"]
