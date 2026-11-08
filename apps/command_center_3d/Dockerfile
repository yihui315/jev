# Self-contained, paper-only cloud staging build.
FROM node:22-bookworm-slim
WORKDIR /app
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN node cloud/verify-engine-vendor.mjs \
 && npm run build \
 && test -s dist/trading.html \
 && test -f server/trading_api.mjs
# An external persistent volume MUST be mounted at /data.
RUN mkdir -p server /data && rm -rf server/runtime && ln -s /data server/runtime
ENV NODE_ENV=production \
    ENABLE_REAL_TRADING=false \
    JCC_ENGINE_DIR=/app/vendor/nr_trusted/functions/_lib \
    JCC_NR_TRUSTED=/app/vendor/nr_trusted \
    JEV_ADAPTER_HEALTH_PATH=/api/jev/health
EXPOSE 8080
CMD ["node","cloud/cloud-entry.mjs"]
