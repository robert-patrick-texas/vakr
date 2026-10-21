# Web access through a reverse proxy

vakr does not speak TLS itself. `vakr-api` serves plain HTTP on loopback (`127.0.0.1:8080`), and a reverse proxy on the same host (nginx here, or the proxy your site already runs) terminates TLS in front of it (SEC-013). Browsers, kiosk displays, and remote integrations connect only to the proxy. The same upstream serves both the JSON API under `/api/` and the [web UI](web-ui.md) at every other path, so one `location /` forwards everything. This page sets that up and shows how to check it.

- [How the pieces fit](#how-the-pieces-fit)
- [1. Tell vakr to trust the proxy](#1-tell-vakr-to-trust-the-proxy)
- [2. Configure nginx](#2-configure-nginx)
- [3. Check it](#3-check-it)
- [A proxy on another host](#a-proxy-on-another-host)
- [What vakr relies on the proxy for](#what-vakr-relies-on-the-proxy-for)

## How the pieces fit

```text
browser ──HTTPS──▶ nginx :443 ──HTTP──▶ vakr-api 127.0.0.1:8080
                   (TLS, HSTS)          /api/v1/...  the JSON API
                                        everything else: the web UI (/opt/vakr/share/web)
                                        believes X-Forwarded-* only from api.trusted_proxies
```

The connection vakr sees always comes from nginx. nginx tells vakr who the client was and that it used HTTPS, in `X-Forwarded-For` and `X-Forwarded-Proto`. Anyone can send those headers, so vakr believes them only when the connection comes from an address listed in `api.trusted_proxies`; from anyone else they are ignored (AUTH-006). What vakr does with them:

- **The scheme decides the session cookie.** Over HTTPS the cookie is `__Host-vakr_session` with `Secure`; a sign-in over plain HTTP from anywhere but loopback is refused ([API: signing in](api.md#signing-in-over-http)).
- **The client address** is what failed sign-ins are counted against (E.12) and what the audit log records. Without the proxy trusted, every client would look like `127.0.0.1`, and one person's failed guesses would slow everyone down.

## 1. Tell vakr to trust the proxy

nginx on the same host connects from loopback. Add to `/etc/vakr/config.toml`:

```toml
[api]
trusted_proxies = ["127.0.0.1/32", "::1/128"]
```

Then validate and restart:

```console
$ vakr config validate
$ sudo systemctl restart vakr-api
```

Leave `api.listen_address` at its default, `127.0.0.1`, so nothing but the proxy can reach vakr's plain-HTTP port. Do not open port 8080 in the firewall.

## 2. Configure nginx

Install nginx and a certificate for the name users will type, for example from your internal CA or from Let's Encrypt. Then create `/etc/nginx/sites-available/vakr` (on Ubuntu; enable it with a link in `sites-enabled`):

```nginx
# vakr behind nginx: TLS here, plain HTTP to vakr-api on loopback (SEC-013).
upstream vakr_api {
    server 127.0.0.1:8080;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name vakr.example.net;
    # Plain HTTP only redirects; vakr refuses sign-in over it anyway.
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name vakr.example.net;

    ssl_certificate     /etc/ssl/certs/vakr.example.net.pem;
    ssl_certificate_key /etc/ssl/private/vakr.example.net.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache   shared:vakr_tls:10m;

    # Browsers use HTTPS for this name from now on (2 years). Add
    # "includeSubDomains" only if every subdomain serves HTTPS.
    add_header Strict-Transport-Security "max-age=63072000" always;

    # vakr bounds request bodies at 64 KiB (api.max_body_bytes); refuse larger here first.
    client_max_body_size 64k;

    location / {
        proxy_pass http://vakr_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # vakr compares Origin with Host to refuse cross-site changes, so keep the Host.
        proxy_set_header Host              $host;
        # Replace, never append: the client's own X-Forwarded-For is not trusted.
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID      $request_id;
        proxy_set_header Forwarded         "";

        proxy_connect_timeout 5s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;
    }
}
```

Notes on the block:

- `proxy_set_header X-Forwarded-For $remote_addr` replaces whatever the client sent. vakr reads the list from the right and stops at the first address that is not a trusted proxy, so `$proxy_add_x_forwarded_for` works as well, but replacing is simpler to reason about. vakr reads `X-Forwarded-*` before `Forwarded` (RFC 7239), so a client's own `Forwarded` header cannot override nginx; clearing it as well keeps it out of vakr entirely. A proxy that sets only `Forwarded` must drop any `X-Forwarded-*` the client sent.
- `X-Request-ID $request_id` makes nginx's request ID the one vakr names in its error answers and log, so one ID finds a request in both logs. Add `$request_id` to nginx's `log_format` to use it.
- vakr sends its own security headers (CSP, `X-Frame-Options`, `nosniff`, referrer policy) on every answer, so nginx needs to add only HSTS. API answers are `Cache-Control: no-store`; the web UI's hashed files under `/_app/immutable/` are cacheable for a year and its pages revalidate (`no-cache`), so nginx needs no caching rules of its own. Do not add CORS headers: the API is same-origin only (SEC-011).
- Keep the whole site on one name and origin. `vakr-api` serves the web UI from the same origin as the API, and its Content-Security-Policy allows nothing else: no other origin, no inline script except SvelteKit's bootstrap by its SHA-256 hash, no inline style. Do not let nginx inject scripts or rewrite pages (`sub_filter`, analytics snippets); the browser would refuse them.

Test the configuration and reload nginx:

```console
$ sudo nginx -t && sudo systemctl reload nginx
```

## 3. Check it

From another machine:

```console
$ curl -sI https://vakr.example.net/api/v1/health/live
HTTP/2 200
strict-transport-security: max-age=63072000
content-security-policy: default-src 'self'; ... frame-ancestors 'none'
x-content-type-options: nosniff
x-frame-options: DENY
cache-control: no-store
x-request-id: 3f0c...
```

Check that the web UI comes through the same upstream, with its CSP:

```console
$ curl -sI https://vakr.example.net/devices
HTTP/2 200
content-type: text/html; charset=utf-8
cache-control: no-cache
content-security-policy: default-src 'self'; script-src 'self' 'sha256-...'; ... frame-ancestors 'none'
```

Then open `https://vakr.example.net/` in a browser and sign in ([web UI](web-ui.md)).

Sign in and look at the cookie. It must be named `__Host-vakr_session` and carry `Secure`, `HttpOnly`, and `SameSite=lax`:

```console
$ read -rs PW
$ curl -s -D - -o /dev/null -H 'Content-Type: application/json' --data-binary @- \
    https://vakr.example.net/api/v1/auth/login <<EOF | grep -i '^set-cookie'
{"username": "alice", "password": "$PW"}
EOF
set-cookie: __Host-vakr_session=vks_...; HttpOnly; Max-Age=43200; Path=/; SameSite=lax; Secure
```

If the cookie is `vakr_session` without `Secure`, vakr did not believe the proxy: check `api.trusted_proxies` and that `vakr-api` was restarted, and look for `[api.untrusted_forwarding]` in `journalctl -u vakr-api`.

Check that plain HTTP straight to vakr is not reachable from outside (it should time out or be refused):

```console
$ curl -s --max-time 5 http://vakr.example.net:8080/api/v1/health/live || echo "not reachable: good"
```

Each sign-in's audit record names the client address. A remote user's sign-in recorded as coming from `127.0.0.1` means vakr is not trusting the proxy.

## A proxy on another host

If TLS ends on a load balancer or proxy on another machine:

1. Set `api.listen_address` to the vakr host's address on the network the proxy uses, for example `10.20.0.15`.
2. Set `api.trusted_proxies` to the proxy's address or subnet, for example `["10.20.0.2/32"]`. List only the proxies; any address in the list can claim to be anyone.
3. Firewall port 8080 so that only the proxy can connect. vakr refuses sign-in over plain HTTP from non-loopback clients, but the proxy's own connection is plain HTTP, and the network between them must be one you trust.
4. Have the proxy send `X-Forwarded-For` (or `Forwarded`) with the client address and `X-Forwarded-Proto: https`, and keep the `Host` header.

## What vakr relies on the proxy for

| The proxy must | Because |
|---|---|
| Terminate TLS with a certificate users trust | vakr serves plain HTTP only (SEC-013) |
| Send HSTS | Browsers then never try plain HTTP |
| Set `X-Forwarded-Proto` and `X-Forwarded-For` (or `Forwarded`), replacing the client's | Cookie flags, sign-in refusal, throttling, and audit use them |
| Keep `Host` | vakr refuses state changes whose `Origin` names another host |
| Not add CORS headers | Other origins must not read the API (SEC-011) |
| Forward request bodies of up to 64 KiB | The largest body vakr accepts |
