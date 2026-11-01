FROM ghcr.io/goauthentik/server:2026.8.4@sha256:e7226ae116dac5aa0a5643803e735b010038f492c401e08e16b3bf1ac54c090c

# Copy instead of bind-mounting: the container can read the blueprint even on
# hosts whose checkout inherits directory ACLs that exclude authentik's UID.
USER root
COPY blueprint.yaml /blueprints/local/serving-api.yaml
RUN chmod 755 /blueprints/local && chmod 644 /blueprints/local/serving-api.yaml
USER authentik
