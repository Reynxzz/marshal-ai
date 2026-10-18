# Deployment: GCP Load Balancer + Cloud Run

## Architecture

```text
browser ──HTTP(S)──> global external Application LB (static IP, optional managed cert)
                         ├─ /* ─────> backend service ─> serverless NEG ─> Cloud Run (marshal-threejs)
                         │                                                 ├─ serve.mjs (Bun): dist/client static + /healthz
                         │                                                 └─ dist/server/server.js: SSR + server functions
                         │                                                       └─> Vertex AI Gemini (runtime service account)
                         └─ /api, /api/* ─> backend service ─> serverless NEG ─> Cloud Run (marshal-api)
                                                                   └─ marshal_api (FastAPI, faris-be): /api/v1/...
                                                                         └─> Vertex AI, Firestore, BigQuery, Pub/Sub, GCS (marshal-ai SA)
GitHub Actions (push to frontend-demo / faris-be) ──OIDC──> Workload Identity Federation ──> deployer SA
    └─ docker build ─> Artifact Registry ─> gcloud run deploy (new revision)
```

- Cloud Run ingress is `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`: the `run.app` URL is not
  reachable from the internet, only through the load balancer.
- Gemini runs on Vertex AI. Cloud Run sets `GOOGLE_GENAI_USE_VERTEXAI=true`,
  `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` and `GEMINI_MODEL`. The runtime service account
  has `roles/aiplatform.user`, so no API key or secret is stored anywhere.
- The browser bundle is built with `VITE_BACKEND_API_URL=/api/v1` (Dockerfile build arg), so
  the simulator calls the Marshal API's `/api/v1/bridge/*` same-origin through the LB (no CORS)
  and falls back to its own server functions when the API fails. See "Marshal API" below.
- The frontend has no database. The batch, event log and reports live in the browser (IndexedDB/localStorage)
  and the simulation runs in the tab. The server is stateless (SSR + Gemini server functions),
  so Cloud SQL or Firestore would add cost and an attack surface with nothing to store. If
  shared history is needed later, add Firestore or Cloud SQL plus a server function to write it.

Terraform lives in [`infra/gcp`](../infra/gcp). The container is built from the root
`Dockerfile` (multi-stage, `oven/bun`, runs `serve.mjs` on port 8080 as the non-root `bun` user).

## 1. Bootstrap infrastructure (once)

Requires Terraform >= 1.6, `gcloud auth application-default login`, and an account that can
enable APIs and manage IAM in the project.

```bash
cp infra/gcp/terraform.tfvars.example infra/gcp/terraform.tfvars   # set project_id, optional domain
terraform -chdir=infra/gcp init
terraform -chdir=infra/gcp plan
terraform -chdir=infra/gcp apply
# or without a tfvars file: terraform -chdir=infra/gcp apply -var project_id=my-gcp-project
```

The first apply deploys the public `us-docker.pkg.dev/cloudrun/container/hello` placeholder.
Terraform ignores later image and label changes so CI deploys never fight Terraform.

State is local (`infra/gcp/terraform.tfstate`, git-ignored). For a team, switch to the
commented `backend "gcs"` block in `versions.tf`.

## 2. Configure GitHub repository variables

All values come from Terraform outputs. WIF means no secrets are needed.

```bash
cd infra/gcp
gh variable set GCP_WIF_PROVIDER      --body "$(terraform output -raw workload_identity_provider)"
gh variable set GCP_DEPLOY_SA         --body "$(terraform output -raw deployer_service_account)"
gh variable set GCP_AR_REPO           --body "$(terraform output -raw artifact_registry_repo)"
gh variable set GCP_REGION            --body "$(terraform output -raw region)"
gh variable set GCP_CLOUD_RUN_SERVICE --body "$(terraform output -raw cloud_run_service)"
gh variable set GCP_API_SERVICE       --body "$(terraform output -raw api_service)"
gh variable set GCP_APP_URL           --body "$(terraform output -raw url)"   # optional
```

| Variable | Terraform output | Example |
| --- | --- | --- |
| `GCP_WIF_PROVIDER` | `workload_identity_provider` | `projects/123/locations/global/workloadIdentityPools/marshal-threejs-gh/providers/github-oidc` |
| `GCP_DEPLOY_SA` | `deployer_service_account` | `marshal-threejs-deploy@my-gcp-project.iam.gserviceaccount.com` |
| `GCP_AR_REPO` | `artifact_registry_repo` | `asia-southeast2-docker.pkg.dev/my-gcp-project/marshal-threejs` |
| `GCP_REGION` | `region` | `asia-southeast2` |
| `GCP_CLOUD_RUN_SERVICE` | `cloud_run_service` | `marshal-threejs` |
| `GCP_API_SERVICE` | `api_service` | `marshal-api` |
| `GCP_APP_URL` | `url` | `https://marshal.example.com` |

## 3. Continuous deployment

`.github/workflows/deploy-cloudrun.yml` runs on every push to `frontend-demo` in
`Reynxzz/marshal-ai` (and manually via `workflow_dispatch` on that branch). Pull requests into
`frontend-demo` run only `verify` plus a `docker build`, so open a PR first: the first push to
`frontend-demo` should not be the first real build.

1. `verify`: `bun install --frozen-lockfile` + `bun run verify` (lint, typecheck, standalone
   guard, tests, build).
2. `deploy`: authenticates with `google-github-actions/auth` through WIF, builds the image,
   pushes `$GCP_AR_REPO/marshal-threejs:<sha>`, and deploys it with
   `google-github-actions/deploy-cloudrun` with `--min-instances=1 --max-instances=1`. These
   flags must match `min_instances` / `max_instances` in Terraform (both default to 1), otherwise
   every deploy overrides the Terraform scaling.

The WIF provider's `attribute_condition` only accepts tokens whose `repository` is
`Reynxzz/marshal-ai` and whose `ref` + `workflow_ref` are one of the `github_deploy_workflows`
pairs (default: `deploy-cloudrun.yml` on `frontend-demo`, `deploy-backend.yml` on `faris-be`).
Other branches, forks, pull requests, any other workflow file merged into a deploy branch, and
one branch's workflow file run from the other branch cannot obtain GCP credentials. Set
`github_repository_id` (`gh api repos/Reynxzz/marshal-ai --jq .id`) to also pin the numeric
repository id, which survives renames and rejects a re-created repository with the same name.
Change `github_repository` / `github_deploy_workflows` in Terraform if the repo, branches or
workflow files change, and keep `on.push.branches` plus the `deploy` job condition of each
workflow in sync. (`github_deploy_workflows` replaces the former `github_branches` and
`github_workflow_file` variables.)

### Marshal API (faris-be)

`api.tf` runs the backend (`marshal_api`, FastAPI) as the Cloud Run service `api_service_name`
(`marshal-api`) behind the same LB: the URL map sends `/api` and `/api/*` to it and everything
else to the frontend. It runs as the `marshal-ai` service account (Vertex AI, Firestore,
BigQuery, Pub/Sub, `storage.objectAdmin` on the artifacts bucket; an existing account is adopted)
with the deployed demo settings of faris-be `.env.production` (`MARSHAL_RUN_PLANT=1`, memory
store and bus, `MARSHAL_LLM=gemini`) plus `GOOGLE_CLOUD_PROJECT`, `MARSHAL_CORS_ORIGINS` (the
domain) and the bucket names; override or add settings with `api_env`. The request timeout is
3600 s for the `/api/v1/events` SSE stream, CPU stays allocated for the in-process plant, and the
Cloud Armor policy also throttles `/api/v1/bridge/*` per IP.

faris-be `.github/workflows/deploy-backend.yml` tests (`uv`, `pytest`) on pull requests and
pushes, and on pushes to `faris-be` builds the faris-be `Dockerfile`, pushes
`$GCP_AR_REPO/marshal-api:<sha>` (same Artifact Registry repository, separate image) and deploys
it with `--min-instances=1 --max-instances=1` (= `api_min_instances` / `api_max_instances`). The
container creates its SQLite demo database at startup, so its data (and the memory store/bus)
resets on every new instance or revision; keep one instance until the state moves to
Firestore/Pub/Sub (`MARSHAL_STORE=gcp`, `MARSHAL_BUS=gcp`) or Cloud SQL (`MARSHAL_DB_URL`).

## 4. Domain and HTTPS (optional)

Set `domain = "marshal.example.com"` and apply. Then create the two DNS records from
`terraform output dns_records`: the `A` record pointing at the LB IP, and the
`_acme-challenge` `CNAME` that authorizes the Certificate Manager certificate (DNS
authorization, so the certificate can be issued before the `A` record points at the LB). The
certificate becomes `ACTIVE` once the CNAME resolves (usually 15 to 60 minutes); check with
`gcloud certificate-manager certificates describe <name_prefix>-cert`. HTTP always redirects to
HTTPS once a domain is set.

If the domain is proxied through Cloudflare (orange cloud), set the Cloudflare SSL/TLS mode to
**Full (strict)**: Flexible connects to the origin over HTTP, which redirects to HTTPS and loops,
and Cloudflare shows 525 until the Google certificate is `ACTIVE`. DNS-only (grey cloud) also
works. Without a domain the
app is served over plain HTTP on the LB IP, including the server function RPC calls, which is
fine for a private demo only: set a domain before sharing the URL.

## 5. Rollback

```bash
gcloud run revisions list --service marshal-threejs --region asia-southeast2
gcloud run services update-traffic marshal-threejs --region asia-southeast2 \
  --to-revisions <previous-revision>=100
```

The next push to a deploy branch deploys a new revision and sends 100% traffic to it again. The
API rolls back the same way with `--service marshal-api`.

## Security and cost notes

- The app is public and unauthenticated through the load balancer (`roles/run.invoker` for
  `allUsers`; ingress still blocks direct `run.app` access). If your organization enforces
  domain-restricted sharing, the `allUsers` binding fails; use
  `invoker_iam_disabled = true` on the service or an org-policy exception instead.
- Every batch and shift can trigger Vertex Gemini calls through public server functions, so
  anyone who finds the URL can spend your quota. Three limits apply: the zod input schemas bound
  every request (at most 8 frames of about 512 KB, 64 parking obstacles, 100 yard slots, 500
  character texts); a Cloud Armor policy on the backend service throttles `/_serverFn/*` to
  `ai_rate_limit_per_minute` requests per client IP (default 120, excess gets HTTP 429 and the
  browser falls back to the offline answers); and `max_instances` (default 1, with `min_instances` 1 for no cold starts) caps concurrency.
  Cloud Armor Standard is billed per policy, rule and request. For anything beyond a demo, put
  the LB behind IAP as well.
- While the dashboard runs on the scripted engine (placeholder frames, not JPEGs), the
  gate, lamp and assist image checks stay offline in the browser; only route, parking and yard
  decisions call Gemini.
- The deployer service account can only push to the app's Artifact Registry repository, deploy
  Cloud Run revisions (`roles/run.developer`) and act as the two runtime service accounts.
- The Marshal API is public and unauthenticated through the LB as well (`allUsers` invoker,
  demo actor), including its write endpoints; put IAP or app-level auth in front before real use.
- Deleting and recreating the WIF pool with the same ID fails for 30 days (soft delete). Change
  `name_prefix` or undelete the pool if that happens.
