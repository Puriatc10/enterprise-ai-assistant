# Build context is the REPO ROOT (see docker-compose.yml).
#
# Two stages: bundle with Node, serve the static output with nginx. The result is
# a static site — the same artifact Render's Static Site hosting would serve.
# (No `# syntax=` directive: it pulls a frontend image from Docker Hub before
# anything else, which is an avoidable dependency on registry availability.)

FROM node:22-alpine AS build

WORKDIR /app

# Dependency layer first, keyed on the lockfile.
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

COPY apps/web/tsconfig.json apps/web/vite.config.ts apps/web/index.html ./
COPY apps/web/src ./src

# Baked into the bundle at build time: VITE_* values are public.
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

RUN npm run build

FROM nginx:alpine AS runtime

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
