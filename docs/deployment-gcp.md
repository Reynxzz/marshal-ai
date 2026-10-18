# Deployment: GCP Load Balancer + Cloud Run

## Architecture

```text
browser ──HTTP(S)──> global external Application LB (static IP, optional managed cert)
                         └─> backend service ─> serverless NEG ─> Cloud Run (marshal-threejs)
                                                                   ├─ serve.mjs (Bun): dist/client static + /healthz
                                                                   └─ dist/server/server.js: SSR + server functions
                                                                         └─> Vertex AI Gemini (runtime service account)
GitHub Actions (push to deploy branch) ──OIDC──> Workload Identity Federation ──> deployer SA
    └─ docker build ─> Artifact Registry ─> gcloud run deploy (new revision)
```

- Cloud Run ingress is `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`: the `run.app` URL is not
  reachable from the internet, only through the load balancer.
- Gemini runs on Vertex AI. Cloud Run sets `GOOGLE_GENAI_USE_VERTEXAI=true`,
  `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` and `GEMINI_MODEL`. The runtime service account
  has `roles/aiplatform.user`, so no API key or secret is stored anywhere.
- No database. The batch, event log and reports live in the browser (IndexedDB/localStorage)
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
gh variable set GCP_APP_URL           --body "$(terraform output -raw url)"   # optional
```

| Variable | Terraform output | Example |
| --- | --- | --- |
| `GCP_WIF_PROVIDER` | `workload_identity_provider` | `projects/123/locations/global/workloadIdentityPools/marshal-threejs-gh/providers/github-oidc` |
| `GCP_DEPLOY_SA` | `deployer_service_account` | `marshal-threejs-deploy@my-gcp-project.iam.gserviceaccount.com` |
| `GCP_AR_REPO` | `artifact_registry_repo` | `asia-southeast2-docker.pkg.dev/my-gcp-project/marshal-threejs` |
| `GCP_REGION` | `region` | `asia-southeast2` |
| `GCP_CLOUD_RUN_SERVICE` | `cloud_run_service` | `marshal-threejs` |
| `GCP_APP_URL` | `url` | `https://marshal.example.com` |

## 3. Continuous deployment

`.github/workflows/deploy-cloudrun.yml` runs on every push to `threejs` or
`feat/qais-marshal-flows-gcp` (and manually via `workflow_dispatch` on those branches). Pull requests into `threejs` run only `verify` plus a `docker build`, so
open a PR first: the first push to `threejs` should not be the first real build.

1. `verify`: `bun install --frozen-lockfile` + `bun run verify` (lint, typecheck, standalone
   guard, tests, build).
2. `deploy`: authenticates with `google-github-actions/auth` through WIF, builds the image,
   pushes `$GCP_AR_REPO/marshal-threejs:<sha>`, and deploys it with
   `google-github-actions/deploy-cloudrun` with `--min-instances=1 --max-instances=1`. These
   flags must match `min_instances` / `max_instances` in Terraform (both default to 1), otherwise
   every deploy overrides the Terraform scaling.

The WIF provider's `attribute_condition` only accepts tokens whose `repository` is
`farissbayu/marshal-fe`, whose `ref` is one of the `github_branches` (`refs/heads/threejs`,
`refs/heads/feat/qais-marshal-flows-gcp`) and whose `workflow_ref` is
`.github/workflows/deploy-cloudrun.yml` on that same branch. Other branches, forks, pull requests
and any other workflow file merged into a deploy branch cannot obtain GCP credentials. Set
`github_repository_id` (`gh api repos/farissbayu/marshal-fe --jq .id`) to also pin the numeric
repository id, which survives renames and rejects a re-created repository with the same name.
Change `github_repository` / `github_branches` / `github_workflow_file` in Terraform if the repo,
branches or workflow file change, and keep `on.push.branches` plus the `deploy` job condition in
the workflow in sync with `github_branches`.

## 4. Domain and HTTPS (optional)

Set `domain = "marshal.example.com"` and apply. Then create a DNS `A` record for the domain
pointing at `terraform output -raw lb_ip`. The Google-managed certificate becomes `ACTIVE` once
DNS resolves (usually 15 to 60 minutes). After that, HTTP redirects to HTTPS. Without a domain the
app is served over plain HTTP on the LB IP, including the server function RPC calls, which is
fine for a private demo only: set a domain before sharing the URL.

## 5. Rollback

```bash
gcloud run revisions list --service marshal-threejs --region asia-southeast2
gcloud run services update-traffic marshal-threejs --region asia-southeast2 \
  --to-revisions <previous-revision>=100
```

The next push to a deploy branch deploys a new revision and sends 100% traffic to it again.

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
  Cloud Run revisions (`roles/run.developer`) and act as the runtime service account.
- Deleting and recreating the WIF pool with the same ID fails for 30 days (soft delete). Change
  `name_prefix` or undelete the pool if that happens.
