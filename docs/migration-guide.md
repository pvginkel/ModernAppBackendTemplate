# Migration Guide: Moving Apps to the Backend Template

This guide documents how to migrate existing Flask apps to use the consolidated backend template.

## Overview

The template provides:
- **Common core**: Flask app factory, settings, error handling, correlation IDs
- **OIDC authentication**: Full BFF pattern with login/callback/logout flows
- **S3 storage**: Ceph/S3-compatible file storage
- **SSE Gateway**: Server-sent events via external gateway
- **Task service**: Background task execution with progress
- **Metrics**: Prometheus metrics collection
- **Health checks**: Liveness and readiness probes
- **Graceful shutdown**: Coordinated service shutdown

## Migration Strategy

### Phase 1: Add Common Module

1. Copy `common/` from generated template to your app
2. Keep your existing `app/` directory intact
3. Update imports gradually

### Phase 2: Adopt Template Patterns

1. Replace custom auth with OIDC (if applicable)
2. Replace custom error handling with template's `@handle_api_errors`
3. Add health endpoints from template
4. Add metrics from template

### Phase 3: Cleanup

1. Remove duplicated code that's now in `common/`
2. Update tests to use shared fixtures

---

## App-Specific: ZigbeeControl Migration

### Current State
- Simple token auth (shared secret)
- 10-year cookie lifetime
- No database
- Internal SSE (not SSE Gateway)
- Services: ConfigService, KubernetesService, StatusBroadcaster

### Target State
- OIDC auth with Remember Me (6-month session)
- Template's common module for core utilities
- Keep existing services (they're app-specific)
- Keep internal SSE (app broadcasts tab status, not task events)

### Changes Required

#### 1. Add Common Module
Copy from template:
- `common/core/` - Flask app, settings, errors, shutdown
- `common/auth/` - OIDC authenticator
- `common/health/` - Health endpoints
- `common/metrics/` - Prometheus metrics

Skip (not needed):
- `common/database/` - ZigbeeControl has no database
- `common/storage/` - No S3 usage
- `common/sse/` - Uses internal SSE, not SSE Gateway
- `common/tasks/` - No background tasks

#### 2. Replace Auth

**Before (.env):**
```
APP_AUTH_TOKEN=secret-token
APP_AUTH_COOKIE_NAME=z2m_auth
```

**After (.env):**
```
OIDC_ENABLED=true
OIDC_ISSUER_URL=https://keycloak.example.com/realms/home
OIDC_CLIENT_ID=zigbee-control
OIDC_CLIENT_SECRET=xxx
BASEURL=https://zigbee.example.com
```

**Delete:**
- `app/utils/auth.py` - Old AuthManager
- `app/schemas/auth.py` - Old schemas

**Add:**
- `app/api/auth.py` - Copy from IoTSupport, simplified (no testing service)

#### 3. Update App Factory

Replace manual service wiring with dependency-injector container:
- Create `app/container.py`
- Update `app/__init__.py` to use `create_app` from common

#### 4. Environment Config

Add to `.env.example`:
```bash
# OIDC Authentication
OIDC_ENABLED=true
OIDC_ISSUER_URL=https://keycloak.example.com/realms/home
OIDC_CLIENT_ID=zigbee-control
OIDC_CLIENT_SECRET=

# Cookie settings
OIDC_COOKIE_NAME=zigbee_auth
OIDC_COOKIE_SAMESITE=Lax

# App base URL (for OIDC redirects)
BASEURL=http://localhost:3000
```

#### 5. Keycloak Setup

Create client `zigbee-control` in Keycloak:
- Client Protocol: openid-connect
- Access Type: confidential
- Valid Redirect URIs: `https://zigbee.example.com/api/auth/callback`
- Web Origins: `https://zigbee.example.com`

Realm Settings for Remember Me:
- SSO Session Idle Remember Me: 180 days
- SSO Session Max Remember Me: 180 days

---

## Keycloak Remember Me Configuration

For apps requiring long sessions (like ZigbeeControl):

### Realm Settings
1. Go to Realm Settings → Sessions
2. Enable "Remember Me"
3. Set "SSO Session Idle Remember Me": 15552000 (180 days in seconds)
4. Set "SSO Session Max Remember Me": 15552000

### Client Settings (optional per-client override)
1. Go to Clients → [your-client] → Advanced
2. Set "Client Session Idle" and "Client Session Max" if different from realm

### Frontend Integration
The login page must include `prompt=login` and handle the "Remember Me" checkbox:
```
/auth/login?redirect=/dashboard&remember_me=true
```

The backend then passes `prompt=login` or `max_age=0` to force fresh auth when needed.

---

## File Mapping

| Template File | Purpose | ZigbeeControl Action |
|---------------|---------|---------------------|
| `common/core/app.py` | App factory | Use as base, extend |
| `common/core/settings.py` | Pydantic settings | Extend with app settings |
| `common/core/errors.py` | Error handling | Use directly |
| `common/core/shutdown.py` | Graceful shutdown | Use directly |
| `common/auth/oidc.py` | OIDC authenticator | Use directly |
| `common/health/routes.py` | Health endpoints | Use directly |
| `common/metrics/` | Prometheus | Use directly |
| `common/database/` | SQLAlchemy | Skip (no DB) |
| `common/storage/` | S3 service | Skip (no S3) |
| `common/sse/` | SSE Gateway | Skip (internal SSE) |
| `common/tasks/` | Task service | Skip (no tasks) |

---

## Testing After Migration

1. Verify health endpoints work: `GET /health/healthz`, `GET /health/readyz`
2. Test OIDC flow: login → callback → authenticated endpoints → logout
3. Verify existing functionality (config, restart, status) still works
4. Check metrics endpoint: `GET /metrics`
