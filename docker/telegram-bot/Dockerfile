FROM node:18-bullseye-slim
WORKDIR /app
COPY package.json .
RUN npm install
COPY bot.js .
CMD ["node", "--dns-result-order=ipv4first", "bot.js"]
