# Copy as apps/command_center_3d/Dockerfile; use that app folder as build context.
FROM node:22-bookworm-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build && test -s dist/trading.html && test -f server/trading_api.mjs
# A Railway (or equivalent) volume MUST be mounted at /data.
# server/runtime holds the SQLite DB, so make it a link to the volume.
RUN mkdir -p server /data && rm -rf server/runtime && ln -s /data server/runtime
ENV NODE_ENV=production ENABLE_REAL_TRADING=false
EXPOSE 8080
CMD ["node", "cloud/cloud-entry.mjs"]
