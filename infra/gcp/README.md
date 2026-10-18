# infra/gcp

Terraform for the production deployment: global external Application Load Balancer, serverless
NEG, Cloud Run (ingress restricted to the LB), Artifact Registry, runtime service account with
Vertex AI access, a Cloud Armor rate limit on the server functions, and Workload Identity
Federation for GitHub Actions (pinned to branch + workflow file pairs). The same LB routes
`/api/*` to the Marshal API (faris-be) on a second Cloud Run service.

| File | Contents |
| --- | --- |
| `versions.tf` | Terraform/provider constraints, commented GCS backend |
| `providers.tf` | Google provider |
| `apis.tf` | Required project APIs, shared labels |
| `artifact_registry.tf` | Docker repository with cleanup policies |
| `cloud_run.tf` | Runtime SA, Cloud Run service, public invoker binding |
| `api.tf` | Marshal API: runtime SA (`marshal-ai`) and roles, Cloud Run service, NEG, backend service |
| `load_balancer.tf` | Global IP, NEG, backend, Cloud Armor rate limits, URL maps (`/api/*` -> API), HTTP(S) proxies, forwarding rules |
| `wif.tf` | WIF pool/provider, deployer SA and its IAM |
| `outputs.tf` | Values for GitHub repository variables |

```bash
terraform -chdir=infra/gcp init
terraform -chdir=infra/gcp apply -var project_id=my-gcp-project
```

Full guide: [docs/deployment-gcp.md](../../docs/deployment-gcp.md).
