# syntax=docker/dockerfile:1
FROM node:20-alpine AS build
WORKDIR /web
COPY ui/web/package.json ui/web/package-lock.json* ./
RUN npm install || npm install --no-package-lock
COPY ui/web ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime
COPY --from=build /web/dist /usr/share/nginx/html
COPY ui/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --retries=3 CMD wget -qO- http://localhost/ || exit 1
