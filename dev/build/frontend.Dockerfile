# Build context is the repository root (the client needs ../reef_api.yaml to
# generate its API types during npm install).
FROM node:22 AS build
WORKDIR /workspace
COPY client/ ./client/
COPY reef_api.yaml ./reef_api.yaml
WORKDIR /workspace/client
RUN npm install && npm run build

FROM node:22
LABEL maintainer="IETF Tools Team <tools-discuss@ietf.org>"
WORKDIR /app
COPY --from=build /workspace/client/.output ./
ENV NITRO_PORT=3000
EXPOSE 3000
CMD ["node", "server/index.mjs"]
