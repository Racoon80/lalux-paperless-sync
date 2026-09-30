# lalux-paperless-sync

Copies your documents from [LALUX](https://www.lalux.lu) easyAPP (Luxembourg
insurance) into [Paperless-ngx](https://docs.paperless-ngx.com), automatically.

It picks up everything the easyAPP customer area offers for download:

| Source      | What                                                   |
|-------------|--------------------------------------------------------|
| `available` | the *Documents* tab: tax certificates and similar      |
| `contracts` | per contract: special conditions, insurance cards      |
| `invoices`  | the PDF behind each premium invoice                    |

New documents are uploaded to Paperless with a tag and correspondent of your
choice. Documents that are already in Paperless (same file checksum) are not
uploaded twice.

> **Unofficial.** LALUX has no public API. This tool talks to the same API as
> the easyAPP web client. It is not affiliated with or endorsed by LALUX, and
> it can break whenever LALUX changes its app. It only reads your own
> documents with your own login.

## How the login works

easyAPP uses a password plus a one-time code (SMS or e-mail). You log in
**once**, interactively; the tool asks LALUX for an *offline* token — the same
one the mobile app uses for its biometric quick login — and stores it in
`/data/state.json`. From then on it runs unattended. The token is renewed on
every sync, so as long as the container runs regularly it stays valid.

If LALUX ever revokes it (password change, logout from all devices), the log
says `LALUX login needed` and the health check turns unhealthy — just log in
again.

## Quick start (Docker Compose)

```yaml
services:
  lalux-paperless-sync:
    image: ghcr.io/racoon80/lalux-paperless-sync:latest
    container_name: lalux-paperless-sync
    restart: unless-stopped
    environment:
      PAPERLESS_URL: http://paperless:8000
      PAPERLESS_TOKEN: your-paperless-api-token
      LALUX_USERNAME: you@example.com
      TZ: Europe/Luxembourg
    volumes:
      - ./data:/data
```

```sh
docker compose up -d
docker exec -it lalux-paperless-sync lalux-paperless-sync login   # password + SMS code
docker exec lalux-paperless-sync lalux-paperless-sync list        # what LALUX offers
docker logs -f lalux-paperless-sync                               # the next sync
```

The password is only needed for `login`; it is asked for interactively and
never stored. You can also pass `LALUX_PASSWORD` for that one command.

The Paperless token is under *Profile → API Auth Token* in Paperless.

## Configuration

| Variable                  | Default                          | Meaning                                                   |
|---------------------------|----------------------------------|-----------------------------------------------------------|
| `PAPERLESS_URL`           | —                                | Paperless base URL, e.g. `http://paperless:8000`          |
| `PAPERLESS_TOKEN`         | —                                | Paperless API token                                       |
| `PAPERLESS_TAGS`          | `LALUX`                          | Comma-separated tags; created if missing                  |
| `PAPERLESS_CORRESPONDENT` | `LALUX`                          | Correspondent; created if missing, empty to leave unset   |
| `PAPERLESS_DOCUMENT_TYPE` | —                                | Optional document type                                    |
| `LALUX_USERNAME`          | —                                | easyAPP login (e-mail), used by `login`                   |
| `LALUX_OTP_TYPE`          | `SMS`                            | `SMS` or `EMAIL`                                          |
| `LALUX_LANGUAGE`          | `fr`                             | `fr`, `de` or `en`                                        |
| `LALUX_SOURCES`           | `available,contracts,invoices`   | Which document sources to sync                            |
| `SYNC_INTERVAL`           | `21600`                          | Seconds between syncs (6 h)                               |
| `SYNC_EXISTING`           | `true`                           | `false` = skip what is there on the first run             |
| `DRY_RUN`                 | `false`                          | Download and log, but upload nothing                      |
| `STATE_DIR`               | `/data`                          | Where the token and the list of synced documents live     |

## Commands

```
lalux-paperless-sync login    one-time interactive login (password + OTP)
lalux-paperless-sync run      sync every SYNC_INTERVAL seconds (container default)
lalux-paperless-sync sync     run one sync and exit
lalux-paperless-sync list     show the documents LALUX offers and whether they are synced
lalux-paperless-sync health   exit 0 if the last sync succeeded recently
```

## Security

`/data/state.json` holds an offline token that gives read access to your
LALUX customer area. It is written with mode `0600`; keep the volume private
and out of backups you share.

## License

MIT
