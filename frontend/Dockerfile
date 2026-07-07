FROM node:24-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .

# VITE_API_URL is set in Railway build env to the backend Railway URL
ARG VITE_API_URL=""
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
# Railway sets $PORT dynamically
CMD sh -c "sed -i 's/listen 80/listen '${PORT:-80}'/' /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"
