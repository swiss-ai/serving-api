FROM ghcr.io/goauthentik/server:2026.8.0-rc6@sha256:173d7d219cca16a262ae07db77092dfbbb540807019f6bfb705aba95c7118971

# Copy instead of bind-mounting: the container can read the blueprint even on
# hosts whose checkout inherits directory ACLs that exclude authentik's UID.
USER root
COPY blueprint.yaml /blueprints/local/serving-api.yaml
RUN chmod 755 /blueprints/local && chmod 644 /blueprints/local/serving-api.yaml
USER authentik
