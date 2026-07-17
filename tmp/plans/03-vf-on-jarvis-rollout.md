# VF on Jarvis: end-to-end rollout runbook

## Purpose

This is the coordination plan after the VF Vercel implementation and Jarvis gateway implementation exist. It prevents either side from being cut over alone.

## Environment mapping

| Environment | Jarvis parent | Gateway iframe origin | VF upstream | Sheet | Secrets |
|---|---|---|---|---|---|
| Local | local Jarvis | local apps host/port | local Flask/Vercel dev | test Sheet | local `.env`, never committed |
| Staging | staging Jarvis | staging apps hostname | immutable Vercel preview/staging URL | test/staging Sheet preferred | staging-only signing/proxy secrets |
| Production | `jarvis.eulerlogistics.com` | `vf-dashboard.apps.jarvis.eulerlogistics.com` | stable production Vercel URL | production Sheet | production-only signing/proxy secrets |

The matching `JARVIS_PROXY_SECRET` value exists in both Jarvis and the VF deployment for each environment. It is different across environments.

## Stage A — VF preview readiness

- [ ] Flask deployment exists on an immutable Vercel preview URL.
- [ ] `/`, `/eligibility` and `/api/*` reject missing/wrong proxy secrets.
- [ ] `/health` reveals no secret or Sheet details.
- [ ] Login UI, `vf_token`, `X-Session-Token`, login/logout routes and Railway URL are gone.
- [ ] All asset, navigation and API URLs work through the dedicated VF gateway hostname.
- [ ] Initial load uses the bootstrap route.
- [ ] Non-2xx reads and mutations produce visible errors.
- [ ] `/api/taif` is implemented or its mutation UI is disabled.
- [ ] No service worker exists.
- [ ] Preview credentials point to the intended Sheet.

## Stage B — Jarvis staging readiness

- [ ] Staging wildcard app DNS/TLS/Ingress reaches `jarvis-api-svc`.
- [ ] Apps-host requests are distinguishable and validated by the backend.
- [ ] Deployed Jarvis cookie-domain behavior has been checked in a browser.
- [ ] Jarvis signing key and `VF_DASHBOARD_PROXY_SECRET` are installed through Kubernetes secrets.
- [ ] VF preview hostname is the only configured upstream for the staging manifest.
- [ ] `external_apps.vf_dashboard.view` and `.manage` exist and sync cleanly.
- [ ] Generic frame route and its error states are deployed.
- [ ] Pilot users have explicit view/manage assignments.

## Stage C — Security verification

- [ ] Authenticated launch contains no Jarvis JWT.
- [ ] Launch ticket expires in 30–60 seconds and cannot launch another app.
- [ ] Direct gateway request without session fails.
- [ ] Expired/tampered session fails.
- [ ] VF origin/session cannot access another external app origin/session.
- [ ] Iframe cannot read Jarvis DOM, local storage, access token or service-worker state.
- [ ] Jarvis refresh/CSRF cookies never reach Vercel.
- [ ] Inbound fake `X-Jarvis-Proxy-Token` is removed and replaced with the configured secret.
- [ ] Redirect, traversal, encoded traversal and private-IP tests fail closed.
- [ ] Upstream `Set-Cookie` is dropped.
- [ ] Browser frame has the intended sandbox and no unapproved permissions.
- [ ] Logs contain no ticket, secret, cookie, Google credential or Sheet row data.

## Stage D — Functional verification

Using a test/staging Sheet:

- [ ] Main dashboard loads all initial data.
- [ ] Loan eligibility page loads without calling Railway directly.
- [ ] One financier/dealer/onboarding update succeeds and is verified in the Sheet.
- [ ] The controlled update is rolled back.
- [ ] One controlled delete succeeds and is restored.
- [ ] Snapshot creation returns once and creates the intended row.
- [ ] TA/IF behaves according to the accepted contract.
- [ ] Read-only user can view but all mutations fail.
- [ ] Manage user can perform approved mutations.
- [ ] Google `429` and timeout simulations produce bounded, understandable failures.
- [ ] Vercel unavailable state appears inside the standard Jarvis frame error UI.

## Stage E — Production deployment order

1. Deploy the production VF version with production environment values.
2. Verify the Vercel origin directly rejects requests without the production proxy secret.
3. Provision and verify production apps DNS/TLS/Ingress.
4. Install the same production-only VF proxy secret in Jarvis.
5. Deploy Jarvis backend with the production VF manifest inactive.
6. Deploy the generic Jarvis frontend route/navigation.
7. Grant view permission to one pilot role and manage to a smaller pilot group.
8. Activate the production manifest.
9. Run read smoke tests.
10. Run one controlled write and roll it back.
11. Observe before expanding RBAC.
12. Retire the previous deployment only after the agreed stability window.

## Monitoring window

During pilot rollout, watch:

- launch and session failures;
- access denials by permission;
- direct-origin `403`s;
- Vercel function duration/cold starts;
- Google rate-limit/authentication failures;
- proxy `502`/`504` rates;
- response size and p95 latency;
- Jarvis CPU/memory and outbound connection usage.

## Rollback decision tree

```text
Data correctness risk?
  -> deactivate VF manifest immediately
  -> stop pilot mutations
  -> inspect/restore Sheet data

VF-only runtime failure?
  -> deactivate manifest
  -> roll Vercel back
  -> reactivate after smoke test

Jarvis gateway regression?
  -> deactivate all external-app manifests
  -> roll Jarvis backend/frontend back

Navigation/UI-only failure?
  -> hide navigation or roll frontend back
  -> gateway remains deny-by-default
```

Rollback must never run `setup_sheets.py`, recreate worksheets or delete production data.

## Definition of done

- VF is accessible only to authorized Jarvis users through the Jarvis interface.
- Direct Vercel HTML and API requests are denied without Jarvis's secret.
- The external app cannot access Jarvis credentials or parent state.
- View and manage access are independently enforced by Jarvis.
- Reads and controlled mutations work through the complete production path.
- Both repositories have automated tests for their side of the interface.
- Operations can deactivate VF without redeploying or modifying the Sheet.
