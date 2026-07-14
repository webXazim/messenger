# Crescentsphere Messenger setup

Production-oriented Django, React, PostgreSQL, Redis, Celery, WebSocket,
WebRTC, and private-object-storage messenger.

This is the only setup guide for the project. Commands below assume Linux for
production and Docker with the Compose plugin.

## Requirements

- Docker Engine and `docker compose`
- Python 3.12 for validation and deployment status parsing
- Node.js 22 and npm for local frontend validation
- A public domain managed by Cloudflare
- A DigitalOcean Droplet with a static public IPv4 address
- A private Cloudflare R2 bucket
- SMTP credentials
- A DNS-only TURN hostname

## Local development

```bash
cp .env.example .env
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

Open `http://localhost:8080`. Apply committed migrations with:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml exec web \
  python manage.py migrate
```

For frontend-only development:

```bash
cd frontend
cp .env.example .env
npm ci
npm run dev
```

## Production architecture

```text
Cloudflare proxied application DNS
        -> HTTPS/WSS -> DigitalOcean Droplet -> Nginx
                                             -> React + Django ASGI
                                             -> PostgreSQL + Redis + Celery
                                             -> private Cloudflare R2

DNS-only TURN hostname -> coturn on the Droplet
```

Do not proxy the TURN hostname through Cloudflare. PostgreSQL, Redis, Django,
and frontend container ports must not be publicly exposed.

## DigitalOcean preparation

Use a supported Ubuntu LTS release, create a non-root sudo user, disable SSH
password authentication after confirming key access, enable automatic security
updates, and install Docker from Docker's official repository.

Configure the DigitalOcean Cloud Firewall:

| Direction | Protocol | Ports | Source |
|---|---|---|---|
| Inbound | TCP | 22 | trusted administrator IPs |
| Inbound | TCP | 80, 443 | Cloudflare IP ranges |
| Inbound | TCP/UDP | 3478 | internet |
| Inbound | UDP | 49160-49200 | internet |
| Outbound | TCP/UDP | required | internet |

Create these DNS records:

- Proxied `A` record for the application domain pointing to the Droplet.
- DNS-only `A` record for the TURN hostname pointing to the Droplet.

In Cloudflare, set SSL/TLS mode to **Full (strict)**. Keep the R2 bucket private
and do not attach a public custom domain to chat media.

## Production configuration

```bash
cp .env.production.example .env
chmod 600 .env
```

The production template is configured for this deployment:

- application URL: `https://crescentsphere.com`
- Droplet: `159.203.29.80`
- standalone local authentication with HS256 JWTs
- no central authentication, payments, or admin service
- private Cloudflare R2 bucket: `mepia`
- Resend SMTP on `smtp.resend.com:587`
- DNS-only TURN endpoint: `turn.crescentsphere.com:3478`

Before editing `.env`, verify `crescentsphere.com` in Resend and create a
Resend API key with sending access. In Cloudflare R2, create an API token with
Object Read & Write access limited to the `mepia` bucket. Never use the global
Cloudflare API key.

Replace each `REQUIRED_*` value. Generate a different value for every local
secret with:

```bash
openssl rand -base64 72
```

Use independent generated values for `SECRET_KEY`, `DB_PASSWORD`,
`TURN_SHARED_SECRET`, and `AUTH_PAYMENT_JWT_SIGNING_KEY`. Set the external
credentials from the provider dashboards:

```env
CLOUDFLARE_R2_ACCOUNT_ID=<Cloudflare account ID>
CLOUDFLARE_R2_ACCESS_KEY_ID=<R2 token access key ID>
CLOUDFLARE_R2_SECRET_ACCESS_KEY=<R2 token secret access key>
EMAIL_HOST_PASSWORD=<Resend API key beginning with re_>
```

Confirm no placeholders remain:

```bash
grep -n 'REQUIRED_' .env
```

That command must produce no output.

Also confirm or configure:

- application domain, HTTPS origins, and Droplet public IP
- independent Django, database, JWT, TURN, and admin secrets
- private R2 account, bucket, and API credentials
- SMTP backend, sender, host, username, and password
- central authentication public key and service credentials, or disable all
  `CENTRAL_*` integrations and use the documented standalone HS256 settings
- Firebase service account if push notifications are enabled

Never commit `.env`, TLS private keys, service-account JSON, or backups.

Create a Cloudflare Origin CA certificate for the application hostname and
install it with:

```bash
./scripts/install-origin-certificate.sh /path/to/origin.crt /path/to/origin.key
```

## Validate before deployment

Run the complete source, frontend, and backend release gates:

```bash
./scripts/validate-release.sh --with-docker
./scripts/production-readiness.sh --preflight
```

Both commands must pass. The preflight verifies production secrets and flags,
TLS, Cloudflare trusted IP ranges, TURN settings, and Compose configuration.

## Deploy

```bash
./scripts/deploy-production.sh
```

The deployment refreshes Cloudflare IP ranges, runs preflight validation,
builds the containers, starts PostgreSQL, Redis, Django, Celery, the frontend,
Nginx and coturn, then probes the deployed stack.

After deployment, run the deep checks:

```bash
./scripts/production-status.sh --probe --deep
./scripts/check-call-production.sh
```

Test registration, email verification, login, direct and group messaging,
uploads, downloads, WebSockets, push notifications, and calls between two real
external networks. Temporarily setting `WEBRTC_ICE_TRANSPORT_POLICY=relay` is a
useful TURN proof; restore it to `all` afterward.

## Backups and recovery

Create and copy backups off the Droplet before upgrades:

```bash
./scripts/backup-postgres.sh
./scripts/backup-media.sh
```

Restore only during a maintenance window:

```bash
./scripts/restore-postgres.sh backups/<database-file>.dump --confirm
./scripts/restore-media.sh backups/<media-file>.tar.gz --confirm
```

Rollback a migration only after restoring compatible application code and
taking a fresh backup:

```bash
./scripts/rollback-migration.sh <app> <migration> --confirm
```

## Routine operations

```bash
# Service and readiness status
./scripts/production-status.sh --probe

# Deep object/media integrity check
./scripts/production-status.sh --probe --deep

# Safe source archive
./scripts/package-release.sh ../messenger-release.zip

# Replace an expiring origin certificate
./scripts/install-origin-certificate.sh <certificate> <private-key>
```

Monitor Droplet CPU, memory, disk, bandwidth, container health, PostgreSQL size,
Redis persistence, Celery failures, coturn allocations, TLS expiration, and
backup restore tests. The default TURN relay range contains 41 UDP ports and
must be expanded for greater concurrent-call capacity.

## Technical references

- `docs/API_CONTRACT.md`
- `docs/API_FRONTEND_GUIDE.md`
- `docs/MESSENGER_UI_ARCHITECTURE.md`


cd ~/csm/messenger

docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.production.yml \
  up -d --no-build --remove-orphans