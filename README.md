# video-evidence-mcp

`video-evidence-mcp` is a self-hosted, read-only MCP service and a `video-evidence` ChatGPT Web/Codex plugin. It searches anonymous public YouTube and Bilibili content and produces a compact evidence package: verified metadata, timestamped captions or local ASR, whole-video distributed frames, scene-change frames, Chinese/English OCR, contact sheets, and bounded window reinspection.

The default deployment listens only on `127.0.0.1:8787`. Long analyses are queued in Redis and executed by a separate worker; an MCP request only enqueues or polls work. No server-side LLM is required. The calling ChatGPT reads the transcript and `ImageContent` contact sheet and writes the final explanation.

Code and state are deliberately separate: the checkout contains only code/configuration, while all persistent service state is bind-mounted below the dedicated host directory `/data/video-evidence-mcp` (`app`, `redis`, `models`, optional Caddy state, and tunnel profile).

## Architecture and data flow

```text
ChatGPT/Codex plugin
        |
        | Secure MCP Tunnel (outbound HTTPS only)
        v
127.0.0.1:8787/mcp  -> MCP service -> SQLite/WAL job + evidence metadata
                                      |
                                      v
                                Redis durable queue
                                      |
                                      v
                                  one worker
                                      |
          URL/DNS guard -> yt-dlp metadata -> Playwright popup handling
                                      |
                  captions -> faster-whisper fallback
                                      |
              FFmpeg distributed + scene frames -> timestamp overlay
                                      |
                    RapidOCR -> evidence selection -> WebP sheets
                                      |
              retain metadata/transcript/OCR/thumbnails; delete raw media
```

The four MCP tools are `search_videos`, `start_video_analysis`, `get_video_analysis`, and `get_video_window`. Every input/output model forbids extra fields. Responses include a trace ID, machine-readable status, warnings, and an error code on failure. `get_video_analysis` and `get_video_window` add a compressed WebP `ImageContent` block when requested.

Security boundaries:

- Input URLs are HTTPS-only canonical YouTube/Bilibili video URLs; playlists, userinfo, non-default ports, and unknown hosts are rejected.
- DNS answers are checked for loopback/private/link-local/reserved addresses. Browser requests are limited to the selected platform and required CDN/API suffixes.
- `TRUSTED_DNS_PROXY_CIDR` is empty by default. A host whose verified transparent proxy maps public names into RFC 2544 benchmarking space may opt into a subnet of `198.18.0.0/15`; arbitrary private CIDRs are rejected by configuration validation, and the platform/redirect host allowlists still apply.
- The adapters only dismiss known close/cancel/continue-without-login/cookie/app prompts. They never enter credentials or bypass CAPTCHA, age, payment, private, or forced-auth controls.
- The private Compose mapping is exactly `127.0.0.1:8787:8787`; Redis has no host port. `AUTH_MODE=none` refuses a non-loopback listener unless `TRUSTED_LOOPBACK_PROXY=true`, which the private Compose deployment uses only behind that loopback mapping.
- The public profile requires an external OIDC/OAuth issuer, validates issuer/audience/scopes/signatures, publishes protected-resource metadata, returns `WWW-Authenticate`, rate-limits requests, bounds concurrency, and redacts sensitive headers/query values. Caddy caps public request bodies at 4 MB.

This implementation follows the current [OpenAI MCP server guide](https://developers.openai.com/plugins/build/mcp-server), [plugin packaging guide](https://developers.openai.com/plugins/build/plugins), [authentication guide](https://developers.openai.com/plugins/build/auth), [ChatGPT connection guide](https://developers.openai.com/plugins/deploy/connect-chatgpt), and [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels). The server uses the current stable v2 line of the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

## Local start

```bash
cp .env.example .env
sudo ./scripts/prepare_data_dir.sh /data/video-evidence-mcp
sudo docker compose build mcp
sudo docker compose up -d --wait redis mcp worker
curl --fail http://127.0.0.1:8787/healthz
curl --fail http://127.0.0.1:8787/readyz
```

No inbound home-network port is opened. Do not change the Compose port mapping to `0.0.0.0:8787` while `AUTH_MODE=none`.

If both `getent ahosts www.youtube.com` and `getent ahosts www.bilibili.com` return synthetic `198.18.x.x` addresses because this host uses a trusted transparent DNS proxy, set `TRUSTED_DNS_PROXY_CIDR=198.18.0.0/15` in the local ignored `.env`. Leave it empty on ordinary DNS.

### YouTube bot verification

The locked yt-dlp installation includes its recommended Deno runtime and matching EJS challenge scripts. This fixes JavaScript challenge extraction, but it cannot clear an IP- or session-level `Sign in to confirm you're not a bot` response. The server fails that condition without repeated extraction attempts and reports `authentication_required`.

If YouTube challenges this server's egress IP, you may opt into a Netscape-format cookie file for public-video extraction. This uses a YouTube account session: yt-dlp warns that the account can be temporarily or permanently banned, so use it only when necessary, keep request volume low, and prefer an account dedicated to this workload. Follow yt-dlp's current cookie-export guidance; do not paste cookies into `.env` or commit them.

```bash
sudo ./scripts/prepare_data_dir.sh /data/video-evidence-mcp
sudo install -o 10001 -g 10001 -m 0600 /path/to/youtube-cookies.txt \
  /data/video-evidence-mcp/secrets/youtube-cookies.txt
```

Then set only the in-container path in the ignored `.env`:

```text
YOUTUBE_COOKIES_FILE=/run/secrets/video-evidence-mcp/youtube-cookies.txt
```

Compose mounts the secrets directory read-only. Each yt-dlp request loads the cookie jar into memory so yt-dlp cannot rewrite the source secret. Leave `YOUTUBE_COOKIES_FILE` empty to retain anonymous-only behavior.

### Bilibili search HTTP 412

Bilibili search first opens the public homepage to obtain a real anonymous `buvid3` guest cookie, then reuses that short-lived in-memory session for the public search API. Cookie values are never logged or persisted. An HTTP 412 response invalidates the cached guest session and is reported as `bilibili search rate limited: HTTP 412`; it is not retried blindly because repeated requests can extend an IP-level restriction.

For development and tests inside the locked image:

```bash
sudo docker compose run --rm --no-deps mcp ruff check .
sudo docker compose run --rm --no-deps mcp mypy src
sudo docker compose run --rm --no-deps mcp pytest
```

## MCP Inspector

The official Inspector CLI can initialize the live Streamable HTTP server and enumerate tools:

```bash
npx -y @modelcontextprotocol/inspector@latest --cli \
  http://127.0.0.1:8787/mcp --transport http --method tools/list
```

For the browser UI, run `npx -y @modelcontextprotocol/inspector@latest`, select Streamable HTTP, and enter `http://127.0.0.1:8787/mcp`. The automated in-memory equivalent is `python scripts/mcp_smoke.py`.

## Secure MCP Tunnel activation

Secure MCP Tunnel is the preferred private route: the server stays loopback-only and `tunnel-client` makes outbound HTTPS requests to OpenAI. A Tunnel ID and a control-plane API key are required.

1. In [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), create or select a tunnel, associate the intended Platform organization and ChatGPT workspace, and grant the operator Tunnels Read + Use (Manage is needed to create/edit).
2. Download the latest `tunnel-client` from the Platform page or the latest public `openai/tunnel-client` release; save it as `deploy/tunnel/tunnel-client`, make it executable, and keep it out of Git.
3. Create `/etc/video-evidence-mcp/tunnel.env` as root with mode `0600`:

   ```text
   TUNNEL_ID=tunnel_...
   CONTROL_PLANE_API_KEY=sk-...
   ```

4. Initialize the profile as the dedicated service user from `/data/video-evidence-mcp/tunnel`:

   ```bash
   cd /data/video-evidence-mcp/tunnel
   set -a
   . /etc/video-evidence-mcp/tunnel.env
   set +a
   /opt/video-evidence-mcp/deploy/tunnel/init-profile.sh
   tunnel-client doctor --profile video-evidence --explain
   ```

5. Install `deploy/systemd/video-evidence-compose.service` and `deploy/systemd/video-evidence-tunnel.service` under `/etc/systemd/system`, then enable them. These are templates; review absolute paths and create the unprivileged `video-evidence` user before installation.

The unit runs `doctor` before `run` and restarts on failure. The `tunnel-client` local admin UI, `/healthz`, `/readyz`, and `/metrics` should remain loopback-only. Secrets never belong in `.env`, Compose YAML, an image, command-line logs, or this repository.

## Add the connection in ChatGPT

Per the current OpenAI flow:

1. Open ChatGPT Settings → Security and login → enable Developer mode (subject to account/workspace policy).
2. Open ChatGPT Plugins, select `+`, enter a name/description, choose **Tunnel**, and select or paste the `tunnel_id`.
3. Review the discovered four tools and create the connection. Refresh metadata after server tool changes.
4. Install/enable the `video-evidence` plugin in the same target account/workspace and test the behavior cases under `evals/plugin-behavior.json`.

The repository marketplace (`marketplace.json`) and local `.mcp.json` are development fixtures. They make the plugin visible to a local Codex/desktop development installation; they do **not** publish or synchronize it to ChatGPT web, desktop, and mobile. Same-account/workspace cross-device use requires creating/installing the corresponding plugin connection in that account/workspace. Public availability requires OpenAI plugin submission/review and a stable public HTTPS endpoint.

To install this repository marketplace in Codex development:

```bash
codex plugin marketplace add /absolute/path/to/video-evidence-mcp
```

After changes, run the cachebuster helper from the installed `plugin-creator` skill and reinstall the plugin; start a new thread so refreshed skill instructions are loaded.

## Optional public HTTPS/OAuth profile

Do not write a password system for this service. Configure a mature external OAuth 2.1/OIDC provider that supports Authorization Code, PKCE S256, the MCP `resource` parameter/audience, required scopes, and either preferred CIMD (`none` or `private_key_jwt`) or DCR. The provider—not this repository—owns login, consent, CIMD/DCR, token issuance, and account security.

Set `DOMAIN`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_REQUIRED_SCOPES`, and optionally `OIDC_JWKS_URL`, point public DNS at the server, and explicitly start only the public services:

```bash
sudo docker compose --profile public up -d --build redis mcp-public worker-public caddy
```

Caddy obtains HTTPS automatically. The MCP endpoint is `https://<domain>/mcp`; metadata is at `https://<domain>/.well-known/oauth-protected-resource/mcp`. Validate the issuer discovery document advertises Authorization Code, PKCE S256, CIMD or DCR as selected, and correct token authentication methods. Validate tokens include the configured audience and scopes. Never expose the private `mcp` service or use `AUTH_MODE=none` on a public listener.

## Maintenance and operations

Upgrade deliberately and regenerate the lock; never update one runtime in place:

```bash
# All Python dependencies, including yt-dlp/faster-whisper/RapidOCR
sudo docker run --rm -e UV_CACHE_DIR=/app/.uv-cache -v "$PWD:/app" -w /app \
  ghcr.io/astral-sh/uv:python3.12-bookworm-slim lock --upgrade

# Prefer Playwright's matching Chromium when its CDN is reachable
sudo docker compose run --rm --user root mcp playwright install chromium

# Rebuild (the image has a distro Chromium fallback for restricted CDNs)
sudo docker compose build --pull --no-cache mcp
sudo docker compose up -d --wait redis mcp worker

# Choose a different ASR model only after sizing CPU/RAM/disk
sed -i 's/^ASR_MODEL=.*/ASR_MODEL=medium/' .env
sudo docker compose up -d worker
```

Back up `/data/video-evidence-mcp` while services are stopped, or use SQLite's online backup API. Evidence metadata is in `/data/video-evidence-mcp/app/video-evidence.sqlite3`, cache files are under `/data/video-evidence-mcp/app/cache`, Redis AOF/RDB files are under `/data/video-evidence-mcp/redis`, and ASR downloads are under `/data/video-evidence-mcp/models`. Restore the matching directory tree and ownership before starting the same application version.

```bash
sudo docker compose logs --since 1h mcp worker
sudo docker compose exec mcp video-evidence-cache disk-check
sudo docker compose exec mcp video-evidence-cache cleanup --dry-run
sudo docker compose exec mcp video-evidence-cache cleanup
```

Cleanup removes only expired/over-limit evidence entries. It never deletes configuration, secrets, the database, Redis state, or ASR models. To uninstall, stop the units/Compose stack first; `docker compose down` leaves `/data/video-evidence-mcp` untouched. Archive that directory before explicitly removing it. Remove `/etc/video-evidence-mcp/tunnel.env` separately and securely.

## Known limitations and troubleshooting

- Platform markup, captions, and anonymous-access policy change. When popup fixtures still pass but live access fails, capture only redacted status/selector diagnostics, update the platform adapter's stable roles/attributes/text, and rerun fixture plus live smoke tests.
- The 2026-08-17 build environment reset every Playwright CDN TLS download, so the verified image explicitly launches Debian Chromium. When CDN access returns, install Playwright's matching browser and remove the executable override during a planned rebuild.
- This host's transparent proxy resolves both platforms into `198.18.0.0/15`; its ignored local `.env` explicitly trusts only that benchmarking CIDR. On another server, remove this setting unless the same mapping is independently verified.
- Region restrictions, rejected bot challenges, forced authentication, age gates, private/paid videos, and live streams are reported as limitations; they are not bypassed. The optional YouTube cookie file supplies an operator-controlled session but does not solve CAPTCHA or override content access controls.
- yt-dlp extraction may break after site changes. Reproduce with `yt-dlp --verbose --skip-download '<canonical-url>'` in the worker image, redact request data, then upgrade/lock/rebuild.
- Automatic captions, Whisper, and OCR can be wrong, especially for proper names, numbers, overlapping speech, stylized text, and low-resolution frames. The Skill requires transcript/visual window cross-checking for important claims.
- Scene detection plus fixed samples gives whole-video coverage, not frame-complete observation. `get_video_window` is capped and returns cached thumbnails, never arbitrary original media.
- The first ASR job downloads the configured model and can take longer. Check worker logs, free disk, and model-volume permissions.
- If Inspector returns `421`, check the Host allowlist and connect to exactly `127.0.0.1:8787`. If readiness is `503`, check Redis health. If a job was interrupted by restart, it is explicitly marked failed and can be resubmitted.
- Optional server-side OpenAI visual description is intentionally disabled by default; the core evidence workflow does not require `OPENAI_API_KEY`.

Live smoke tests are opt-in because they contact third-party platforms:

```bash
RUN_LIVE_TESTS=1 pytest -m live -vv
python scripts/live_smoke.py
python scripts/live_analysis_smoke.py
```

Results are written under `test-results/` with the URL, UTC date, result, and exact error class. A blocked or rate-limited live test is recorded as such, never reported as passed.
