FROM node:20-alpine AS build

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
ENV AIDP_API_PREFIX=/api/v1
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/docker-entrypoint.d/10-render-aidp-nginx.sh /docker-entrypoint.d/10-render-aidp-nginx.sh
RUN chmod +x /docker-entrypoint.d/10-render-aidp-nginx.sh
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
