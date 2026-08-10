FROM node:24-bookworm-slim

WORKDIR /app

# NEXT_PUBLIC_* vars must be declared as ARGs before the build step so they
# are inlined into the client bundle. The compose `build.args` block passes
# the runtime values from .env into this stage.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE=self-hosted
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID=""
ARG NEXT_PUBLIC_EMAIL_ENABLED=true
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE=$NEXT_PUBLIC_DAEMON_DEPLOYMENT_MODE
ENV NEXT_PUBLIC_GOOGLE_CLIENT_ID=$NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NEXT_PUBLIC_EMAIL_ENABLED=$NEXT_PUBLIC_EMAIL_ENABLED

COPY package*.json ./
RUN npm ci

COPY . .
ENV NODE_ENV=production
RUN npm run build

EXPOSE 3000

CMD ["npm", "run", "start"]
