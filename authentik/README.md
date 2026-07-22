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
| `blueprints/serving-api.yaml` | yes | The serving-api blueprint `apply.py` pushes: scope mapping, groups, `cilogon-direct` flow, both OAuth2 providers, both applications. References the CILogon source (does not manage it). |
| `blueprints/cilogon-source.yaml` | yes | The shared CILogon source definition, for fresh/self-host instances. **Not** pushed by `apply.py` (it holds the shared write-only secret). |
| `apply.py` | yes | Pushes `serving-api.yaml` to Authentik as a content-based instance and applies it (the propagation step). |
| `test/` | yes | Disposable local Authentik + scripts that validate the blueprint (`test/run.sh`) and `apply.py` (`test/run.sh --apply`). |
| `.token` | no (git-ignored) | Admin API token used by `export.py` / `apply.py`. |
| `raw/` | no (git-ignored) | Raw API dumps + verbatim flow exports; an inspection aid. |

### Secrets

The blueprint contains **no secret values**. Secrets are `!Env` references
resolved at apply time; `apply.py` substitutes them into the content before
pushing and **refuses to apply if either is unset**, so an empty value can never
overwrite a live secret:

- `AUTHENTIK_SERVING_PROD_CLIENT_SECRET`
- `AUTHENTIK_SERVING_DEV_CLIENT_SECRET`

The **CILogon `consumer_secret` is intentionally not managed by `apply.py`**: the
CILogon source is shared, pre-existing infrastructure and its secret is
write-only (unreadable over the API), so overwriting it would risk breaking
federation for every app. `serving-api.yaml` references the source with `!Find`;
its definition (with `AUTHENTIK_CILOGON_CLIENT_SECRET`) lives in
`cilogon-source.yaml`, applied only where you hold that secret.

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

## Applying the blueprint (propagation)

`ak export_blueprint`/`ak apply_blueprint` run inside the Authentik worker pod,
which we don't have cluster access to. The path that works with just an API
token is a **content-based blueprint instance**, which is what `apply.py` uses:

```sh
export AUTHENTIK_SERVING_PROD_CLIENT_SECRET=...   # from your secret store
export AUTHENTIK_SERVING_DEV_CLIENT_SECRET=...
python authentik/apply.py --dry-run   # push disabled, let Authentik validate
python authentik/apply.py             # create/update the instance + apply
```

`apply.py` substitutes the two provider secrets into the content, pushes it as
the `serving-api` blueprint instance, and triggers an apply; the worker then
keeps re-applying on its schedule, so a committed change converges on the live
instance. This is the "edit repo → run apply → propagates" loop. Wire it into CI
(on push to `main`, with the token + secrets from CI secrets) for automatic
propagation.

Apply is a **partial update** — Authentik only writes the fields the blueprint
names (to the values exported from the live instance) and leaves every other
field untouched; the shared CILogon source is referenced, not written. So the
first real apply against the live instance is effectively a no-op. Run
`--dry-run` first and eyeball the diff in the UI. There is no one-click rollback,
but `raw/` is a full pre-apply snapshot to restore from if needed.

Alternative — **mounted secret (SDSC style):** if you ever self-host Authentik,
mount the blueprint as a `Secret` keyed `*.yaml`, list it under the Authentik
Helm chart's `blueprints:` value, and the worker auto-discovers + applies it
(secrets come from the worker env via the `!Env` tags). This is the path the
local discovery test exercises. See `templates/authentik-blueprints-secret.yaml`
in the SDSC repo.

## Testing

Both paths are validated against a disposable local Authentik (pinned to the
live version); nothing touches the shared instance:

```sh
authentik/test/run.sh          # discovery path: mount blueprint, assert objects + idempotency
authentik/test/run.sh --apply  # push path: run apply.py, assert fail-closed + apply + idempotency
authentik/test/run.sh --down   # tear down
```
