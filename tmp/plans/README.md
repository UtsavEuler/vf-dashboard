# External VF deployment planning

These plans describe a small-first rollout. They do not require a frontend rewrite or a database-backed external-app registry.

1. [VF dashboard Vercel deployment](01-vf-dashboard-vercel-deployment.md)
2. [Jarvis external-app gateway](02-jarvis-external-apps-gateway.md)
3. [VF-on-Jarvis rollout runbook](03-vf-on-jarvis-rollout.md)

## Decisions already made

- `vf-dashboard` remains a separate repository and deployment.
- Jarvis gets a reusable top-level `external_apps` module; it does not go inside the existing Jarvis Vehicle Financing module.
- Jarvis remains the user-authentication and RBAC seam.
- The VF deployment trusts only Jarvis's injected per-app proxy secret.
- The Jarvis page stays on `https://jarvis.eulerlogistics.com`, while each iframe gets an isolated app origin such as `https://vf-dashboard.apps.jarvis.eulerlogistics.com`.
- Initial app registration is Git-backed and reviewed. A database registry and self-service UI are deferred.
- Planning only: none of the implementation described here has been applied.
