# Jarvis: phased external-app gateway plan

## Outcome

Add a reusable top-level `external_apps` module to Jarvis. It discovers permitted apps, creates secure iframe launch sessions and proxies configured apps without exposing Jarvis credentials or upstream secrets.

This is independent of Jarvis's existing `vehicle_financing` and legacy `internal_apps` modules.

```text
https://jarvis.eulerlogistics.com/apps/vf-dashboard
  -> authenticated POST /api/v1/external-apps/vf-dashboard/launch
  -> https://vf-dashboard.apps.jarvis.eulerlogistics.com/_session
  -> app-scoped gateway session
  -> fixed Vercel upstream
```

## Scope decision

Start with reviewed Git-backed manifests. Do not build registry tables or a self-service administration UI yet.

This provides leverage through a small interface:

```text
GET  /api/v1/external-apps
POST /api/v1/external-apps/{slug}/launch
ANY  https://{slug}.apps.jarvis.eulerlogistics.com/{path...}
```

The implementation hides manifest parsing, RBAC, launch tickets, gateway cookies, header filtering, SSRF defense, upstream secrets, streaming, limits and errors. Removing the module would force every external app to reproduce that complexity, so the module passes the deletion test.

## Phase 0 — Infrastructure and trust contract

### Hosts

- Parent application: `https://jarvis.eulerlogistics.com`.
- Production iframe/gateway template: `https://{slug}.apps.jarvis.eulerlogistics.com`.
- Staging iframe/gateway template: choose and standardize one wildcard domain, for example `https://{slug}.apps.staging-jarvis.eulerlogistics.com`.

Do not serve employee-built app JavaScript on the main Jarvis origin. Give every app its own hostname. This prevents access to Jarvis state and prevents one external app from reusing another app's same-origin storage or gateway cookie.

### External infrastructure work

Jarvis's Helm chart currently contains Deployment and Service resources but no Ingress resource. Coordinate outside this repository for:

- wildcard DNS and TLS for both app-host templates;
- wildcard host routing from `*.apps...` to `jarvis-api-svc`;
- streaming behavior, request body limits and upstream timeouts at the load balancer/Ingress;
- Jarvis pod egress to approved Vercel hostnames;
- host-preservation or forwarded-host behavior needed for exact host validation.

Iframe navigation does not need CORS. Do not add the apps origin to Jarvis CORS merely to make embedding work.

### Cookie prerequisite

Jarvis supports `AUTH_REFRESH_COOKIE_DOMAIN`; production may use `.eulerlogistics.com`, which would cause refresh/CSRF cookies to be sent to the apps subdomain.

Before rollout:

- verify deployed cookie domain settings;
- prefer host-only Jarvis refresh cookies if cross-subdomain auth does not require the broad domain;
- regardless, ignore and strip every inbound Jarvis cookie on gateway routes;
- never forward Jarvis refresh/CSRF cookies upstream;
- set a different, host-only gateway cookie scoped to `/` on that app's dedicated hostname.

### Exit criteria

- A staging app hostname resolves through wildcard DNS/TLS and reaches the Jarvis backend.
- Gateway host validation behavior is known behind the real Ingress.
- Cookie-domain risk is tested in a browser.

## Phase 1 — Settings and manifest contract

### New files

```text
backend/app/core/settings/domains/external_apps.py
backend/app/api/modules/external_apps/
  __init__.py
  schema.py
  repository.py
  manifests/
    vf-dashboard.yaml
```

### Modified files

```text
backend/app/core/settings/domains/__init__.py
backend/app/core/settings/main.py
backend/.env.staging
backend/.env.production
```

### Settings interface

```dotenv
EXTERNAL_APPS_PUBLIC_ORIGIN_TEMPLATE=https://{slug}.apps.jarvis.eulerlogistics.com
EXTERNAL_APPS_ALLOWED_HOST_SUFFIX=.apps.jarvis.eulerlogistics.com
EXTERNAL_APPS_SESSION_SIGNING_KEY=
EXTERNAL_APPS_LAUNCH_TTL_SECONDS=30
EXTERNAL_APPS_SESSION_TTL_SECONDS=900
EXTERNAL_APPS_DEFAULT_TIMEOUT_SECONDS=15
EXTERNAL_APPS_MAX_REQUEST_BYTES=1048576
EXTERNAL_APPS_MAX_RESPONSE_BYTES=10485760
VF_DASHBOARD_PROXY_SECRET=
VF_DASHBOARD_UPSTREAM_ORIGIN=https://<environment-specific-vf-host>.vercel.app
```

Kubernetes receives these through the existing `jarvis-api-secrets` deployment path. Preview/staging and production use separate signing keys and app secrets.

### Manifest interface

```yaml
apiVersion: jarvis.eulerlogistics.com/v1
kind: ExternalApp
metadata:
  slug: vf-dashboard
  name: Vehicle Finance Dashboard
  description: Vehicle-finance operations and eligibility tools
  owner: vehicle-finance
  supportEmail: <team-address>
spec:
  upstream:
    originRef: VF_DASHBOARD_UPSTREAM_ORIGIN
    healthPath: /health
    credentialRef: VF_DASHBOARD_PROXY_SECRET
  entrypoint:
    path: /
    pathMode: relative
  navigation:
    label: Vehicle Finance
    group: Tools
    icon: landmark
    order: 40
  access:
    viewPermission: external_apps.vf_dashboard.view
    managePermission: external_apps.vf_dashboard.manage
    allowedMethods: [GET, HEAD, POST, DELETE]
  limits:
    timeoutSeconds: 15
    maxRequestBytes: 1048576
    maxResponseBytes: 10485760
```

The manifest contains environment references, never secret values or a production hostname reused accidentally by staging. Resolve `originRef` at startup and apply the same HTTPS/hostname/IP validation as a literal origin.

### Validation

Fail startup/CI on:

- duplicate or invalid slugs;
- unknown schema fields/version;
- non-HTTPS or IP-literal upstreams;
- localhost, private, link-local, metadata or cluster-internal targets;
- unknown permission codes, navigation groups or icon keys;
- unsafe/unsupported methods;
- limits above platform ceilings;
- missing owner/support/credential reference.

Runtime target validation remains mandatory; manifest validation is not the SSRF control by itself.

## Phase 2 — RBAC and catalog

### Modified/new files

```text
backend/app/core/permissions/registry.py
backend/app/core/permissions/scopes/external_apps.py
backend/app/core/permissions/scopes/__init__.py
backend/tests/unit/permissions/test_registry.py
```

### Initial permissions

```text
external_apps.vf_dashboard.view
external_apps.vf_dashboard.manage
```

- `view` permits catalog visibility, launch and read methods.
- `manage` permits approved mutation methods.
- Define both in Jarvis's code-owned permission registry so startup sync remains deterministic.
- Mark `manage` dangerous if that matches existing control-panel presentation.
- Add each future manifest's permissions through a reviewed code change initially.

Do not introduce dynamic database-owned permission codes in v1. That would require changing the existing permission catalog drift model and adds unnecessary scope.

### Exit criteria

- Registry composition and startup sync tests pass.
- Pilot roles can receive view/manage independently.
- Removing frontend navigation does not bypass backend permission enforcement.

## Phase 3 — Backend control interface

### New files

```text
backend/app/api/modules/external_apps/router.py
backend/app/api/modules/external_apps/schema.py
backend/app/api/modules/external_apps/service.py
backend/app/api/modules/external_apps/repository.py
```

Modify `backend/app/api/routers/v1.py` to include the authenticated control router.

### Interface

```text
GET  /api/v1/external-apps
     -> only apps the current principal may view

POST /api/v1/external-apps/{slug}/launch
     -> permission check + short-lived launch result
```

Response:

```json
{
  "launch_url": "https://vf-dashboard.apps.jarvis.eulerlogistics.com/_session",
  "expires_at": "...",
  "sandbox": "allow-scripts allow-same-origin allow-forms allow-downloads"
}
```

Prefer a hidden form POST launch in the frontend if practical so the ticket does not appear in URL logs/history/referrers. If a query ticket is used initially, make it single-use, 30–60 seconds, immediately exchange it and redirect to a clean URL.

### Module responsibilities

- Router: FastAPI parsing and response construction only.
- Service: access decisions, launch claims, session validation, method policy and orchestration.
- Repository: Git-backed manifest loading/lookup per Jarvis convention.
- Private upstream adapter: `httpx` streaming and header translation.

Use the existing `InternetHttpxClientDep` initially. Introduce a separate HTTP client seam only when different pooling/limits are actually required.

### Exit criteria

- Catalog hides unauthorized apps.
- Launch never includes the Jarvis access JWT.
- Tickets are audience-, user- and app-bound and expire quickly.

## Phase 4 — Gateway data path

### Route registration

Export a second router from `external_apps` and register it directly in `backend/app/api/routers/root.py`:

```text
POST /_session
ANY  /{path:path}
```

Register these routes only for validated external-app hosts. The host's slug selects the manifest; the request path never selects an upstream host. Keep the data path outside `/api/v1`, so both `/api/...` and relative external-app assets remain natural.

### Launch/session flow

1. Jarvis frontend calls authenticated `POST /api/v1/external-apps/{slug}/launch`.
2. Backend checks live view permission.
3. Backend creates a signed, short-lived, app/user/audience-bound ticket.
4. Iframe posts the ticket to `https://{slug}.apps.../_session`.
5. Gateway validates it, verifies that the ticket slug matches the hostname, sets a host-only cookie and returns `303 /`.
6. All subsequent iframe requests carry only the app-scoped gateway session.

Cookie:

```text
HttpOnly; Secure; SameSite=Strict; Path=/
```

Use a signed 10–15 minute session initially. Mutations should re-check live permission; short expiry bounds revocation delay for reads. If strict one-time ticket replay prevention is required, add a small persistence adapter only for ticket nonces rather than a full app registry database.

### Required proxy controls

- Parse and validate the app slug from the exact wildcard hostname; reject unknown/malformed hosts.
- Resolve upstream only from a validated manifest; never from browser URL/header/query data.
- Normalize and reject absolute URLs, `//host`, traversal and encoded traversal bypasses.
- Resolve/revalidate DNS and reject private, loopback, link-local and metadata addresses.
- Disable redirects or validate every redirect destination.
- Strip `Authorization`, all inbound cookies, `Host`, hop-by-hop headers, `Forwarded`, `X-Forwarded-*` and inbound `X-Jarvis-*`.
- Inject only the configured per-app `X-Jarvis-Proxy-Token`.
- Drop upstream `Set-Cookie` initially.
- Enforce allowed methods, body/response limits, timeouts and concurrency protection.
- Preserve useful upstream status codes; map connection failure to stable `502` and timeout to `504`.
- Stream rather than fully buffer large responses, while still enforcing limits.
- Do not log tickets, cookies, secrets, bodies or Google payloads.
- Add structured fields: app slug, actor ID, method class, normalized path, upstream status, latency, bytes and outcome.
- Set `Content-Security-Policy: frame-ancestors https://jarvis.eulerlogistics.com` and staging equivalent, `X-Content-Type-Options: nosniff`, a narrow `Permissions-Policy`, and `Referrer-Policy: no-referrer`.
- Explicitly reject WebSocket/protocol upgrades in v1.

### Exit criteria

- Direct gateway access without an app session fails.
- App A session cannot access app B.
- External app cannot see Jarvis credentials or the upstream secret.
- Inactive/removed manifests reject existing sessions on their next request.

## Phase 5 — Generic Jarvis frontend

### New files

Use actual repo naming conventions when implementing, approximately:

```text
frontend/apps/web/src/services/external-apps/types.ts
frontend/apps/web/src/services/external-apps/api.ts
frontend/apps/web/src/services/external-apps/query.ts
frontend/apps/web/src/pages/external-apps/external-app-page.tsx
frontend/apps/web/src/routes/_authenticated/apps/$appSlug.tsx
```

Modify:

```text
frontend/apps/web/src/navigation/app-navigation.ts
```

### Generic frame behavior

1. Validate route access using existing navigation/RBAC helpers.
2. Fetch the accessible app catalog.
3. Request a launch result through the authenticated Axios client.
4. Establish the iframe session.
5. Render standard loading, access-denied, expired-session and unavailable states.

Default frame:

```tsx
<iframe
  title={app.name}
  sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
  referrerPolicy="no-referrer"
/>
```

`allow-same-origin` is acceptable here because every app receives a different origin and the parent Jarvis origin is different. Do not collapse apps back onto one shared origin. Popups, clipboard, camera, microphone and location remain disabled initially; capabilities become reviewed manifest fields only when a real app needs them.

For the first release, choose one of these navigation approaches:

- lowest risk: a static VF route definition plus the generic dynamic route;
- more reusable: merge the accessible catalog into navigation through a constrained icon/group mapping.

Recommendation: use the static VF navigation entry for the pilot, then make catalog navigation dynamic after the frame and RBAC behavior are stable.

### Exit criteria

- Unauthorized users cannot see or launch VF.
- Frame loading/failure does not break the Jarvis shell.
- The iframe cannot read parent DOM or local storage in browser tests.

## Phase 6 — Tests

### Backend unit/integration tests

Add tests under `backend/tests/unit/external_apps/` plus app-wiring tests for:

- manifest schema and target validation;
- missing view/manage permission;
- app/user/audience ticket binding, expiry and tampering;
- replay behavior;
- wrong host;
- inactive or unknown app;
- cross-app cookie reuse;
- mutation permission revocation;
- header/cookie stripping and secret injection;
- traversal and encoded traversal;
- redirect SSRF and private-IP/DNS cases;
- method, body, response and timeout limits;
- streaming cleanup;
- `502`/`504` mapping and upstream status preservation;
- router registration in both `v1.py` and `root.py`;
- permission registry composition.

### Frontend tests

- accessible/hidden navigation;
- launch success/failure;
- loading and unavailable states;
- iframe sandbox/referrer attributes;
- session expiry and relaunch;
- no Jarvis token in launch URL or frame messages.

### Browser security tests

- iframe origin differs from Jarvis and every other external app origin;
- parent DOM/local storage/access token are unreadable;
- Jarvis refresh/CSRF cookies are not forwarded upstream;
- service worker on Jarvis cannot intercept the apps origin;
- stored-XSS payload from VF cannot execute in the parent shell;
- default browser permissions are unavailable.

## Phase 7 — Jarvis deployment setup

### Staging order

1. Provision wildcard DNS/TLS/Ingress for the staging app-host template.
2. Add staging settings and secrets through the existing Jarvis secret deployment flow.
3. Deploy backend gateway with the VF manifest disabled or pointed only at the preview upstream.
4. Sync permissions and grant pilot access.
5. Deploy the generic frontend route through the existing S3/CloudFront script.
6. Activate the VF staging manifest.
7. Run the end-to-end rollout plan.

### Production order

1. Provision/verify `*.apps.jarvis.eulerlogistics.com` DNS, wildcard TLS and host routing.
2. Add production signing key and VF proxy secret to `jarvis-api-secrets`.
3. Deploy backend and verify `/health` plus host routing.
4. Deploy frontend to the existing S3/CloudFront distribution.
5. Add/activate the production VF manifest.
6. Grant view to a pilot role; grant manage more narrowly.
7. Observe before expanding access.

### Rollback

- First deactivate/remove the app manifest to stop launches and proxying.
- Revoke its permissions if needed.
- Roll back Jarvis backend/front end independently through existing deployment mechanisms.
- DNS may remain in place; an inactive gateway must still deny traffic.

## Phase 8 — Operational follow-up

Monitor per app:

- launches, launch failures and ticket failures;
- denied view/manage checks;
- upstream `4xx`, `5xx`, `502`, `504`;
- upstream latency and response size percentiles;
- concurrent proxy requests and Jarvis pod resource usage;
- SSRF/path/header validation rejections.

If proxy traffic materially affects normal Jarvis requests, the next adapter is a separately scaled gateway deployment using the same module interface—not a rewrite of each app integration.

## Deferred work

- Database registry and admin UI.
- Self-service onboarding.
- Dynamic database-owned permission codes.
- Health-history tables and lifecycle audit UI.
- Identity forwarding to external apps.
- WebSockets, SSE, upstream cookies and HTML/JavaScript rewriting.
- Arbitrary iframe or proxy capabilities.
