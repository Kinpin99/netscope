# Security Notes

This project is intended for an internal school LAN, but the dashboard still exposes useful network intelligence and administrative controls. The API therefore implements the realistic minimum production controls:

## Implemented

- **Authentication:** `/auth/login` issues bearer tokens signed with `NETSCOPE_JWT_SECRET`.
- **Password storage:** local users are stored in `data/security/users.json` using PBKDF2 hashes.
- **Role-based access control:** read-only dashboard endpoints require any authenticated user; model retraining and per-device baseline changes require the `admin` role.
- **Login rate limiting:** repeated failed logins are locked for 15 minutes after 5 failures by default.
- **CORS restriction:** allowed origins come from `config.yaml` or `NETSCOPE_CORS_ORIGINS`.
- **LAN/IP allow-list:** can be enabled with `NETSCOPE_ENFORCE_IP_ALLOWLIST=true` and `NETSCOPE_IP_ALLOWLIST`.
- **Audit logging:** login attempts, blocked registrations, retraining, baseline changes, and IP allow-list denials are logged to `data/audit/audit.log`.
- **Frontend session handling:** the dashboard validates saved sessions, attaches bearer tokens, logs out on 401 responses, and times out after 30 minutes of inactivity.

## Required production environment variables

```bash
export NETSCOPE_JWT_SECRET="use-a-long-random-secret"
export NETSCOPE_BOOTSTRAP_USERNAME="admin"
export NETSCOPE_BOOTSTRAP_PASSWORD="set-a-strong-password"
export NETSCOPE_CORS_ORIGINS="https://netscope.school.local"
```
For Windows(Powershell), use "$env:NETSCOPE..."

Optional LAN restriction:

```bash
export NETSCOPE_ENFORCE_IP_ALLOWLIST="true"
export NETSCOPE_IP_ALLOWLIST="127.0.0.1/32,192.168.0.0/16,10.0.0.0/8"
```

Self-registration is disabled by default. Enable it only for controlled testing:

```bash
export NETSCOPE_ALLOW_REGISTRATION="true"
```

## HTTPS

Serve the frontend through a reverse proxy with HTTPS enabled, and proxy `/api/*` to the FastAPI backend. Keep the backend bound to localhost or a protected management subnet where possible.
