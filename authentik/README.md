# Authentik config-as-code

The serving-api authenticates against the shared CSCS Authentik at
`https://auth.swissai.svc.cscs.ch`. This directory captures the **serving-api
slice** of that Authentik config as a version-controlled blueprint, so the setup
is reviewable/diffable and no longer lives only in the admin UI.

This repo **consumes** Authentik (it's an OIDC client); it does not deploy it.
So unlike the [SDSC deployment][sdsc] — which owns Authentik as a Helm
dependency and ships the blueprint inside its chart — we keep just the blueprint
here. The apply path is documented below.

[sdsc]: https://github.com/SwissDataScienceCenter/sdsc-llm-deployment/tree/azure-deployment

## Files

| Path | Committed | What |
|------|-----------|------|
| `export.py` | yes | Pulls the live serving-api config over the Authentik REST API into `raw/`. |
| `blueprints/serving-api.yaml` | yes | The whole serving-api blueprint: scope mapping, groups, CILogon source, the `cilogon-direct` flow, both OAuth2 providers, both applications. |
| `test/` | yes | Disposable local Authentik + assertion script that validates the blueprint (`test/run.sh`). |
| `.token` | no (git-ignored) | Admin API token used by `export.py`. |
| `raw/` | no (git-ignored) | Raw API dumps + verbatim flow exports; an inspection aid. |

### Secrets

The blueprints contain **no secret values**. Client secrets and the CILogon
consumer secret resolve at apply time from environment variables via Authentik's
`!Env` tag — inject these from your secret store:

- `AUTHENTIK_CILOGON_CLIENT_SECRET`
- `AUTHENTIK_SERVING_PROD_CLIENT_SECRET`
- `AUTHENTIK_SERVING_DEV_CLIENT_SECRET`

OAuth `client_id` values are public OIDC identifiers (they appear in browser
flows), so they are kept inline.

## Re-exporting after a UI change

1. Create an admin API token: Authentik UI → **Directory → Tokens and App
   passwords → Create**, Intent = **API**. Save it:
   ```sh
   echo 'YOUR_TOKEN' > authentik/.token   # git-ignored
   ```
2. Run the exporter (stdlib + `requests` only):
   ```sh
   python authentik/export.py
   ```
3. Diff `raw/` against `blueprints/serving-api.yaml` and update the blueprint.
   Secrets (client secret, signing keys) are **not** exported by Authentik and
   must stay as placeholders — never commit real secret values.

## Applying the blueprint

`ak export_blueprint`/`ak apply_blueprint` run inside the Authentik worker pod,
which we don't have cluster access to. Options, in order of preference:

- **Blueprint instance via the UI/API** — Authentik → *Customization →
  Blueprints → Create* with the file contents (or `POST
  /api/v3/managed/blueprints/`). Authentik validates and applies it, and shows a
  dry-run diff first. The blueprint is a single self-contained file — entries
  are ordered so every `!Find` resolves on a first apply.
- **Mounted secret (SDSC style)** — if you ever run your own Authentik, mount
  the blueprint as a `Secret` keyed `*.yaml` and list it under the Authentik
  Helm chart's `blueprints:` value; the worker auto-applies it. See
  `templates/authentik-blueprints-secret.yaml` in the SDSC repo.

Secrets themselves belong in the deployment's secret store (the SDSC repo keeps
them in sops-encrypted `values.*.enc.yaml`), not in this blueprint.
