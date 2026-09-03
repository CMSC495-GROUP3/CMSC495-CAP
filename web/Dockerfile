# Stage 1 — build the React app
# Build context is web/ so all paths here are relative to that directory.
FROM node:26-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2 — serve with Nginx
FROM nginx:alpine

# The official image renders /etc/nginx/templates/*.template at startup.
# docker-compose.yml restricts envsubst to EDGE_SUBNET so Nginx variables such
# as $remote_addr and $host remain untouched.
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Copy the built React app from Stage 1
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
