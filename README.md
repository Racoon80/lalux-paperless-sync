# lalux-paperless-sync

**Automatically copy your documents from LALUX easyAPP into Paperless-ngx.**

[LALUX](https://www.lalux.lu) is an insurance company in Luxembourg. Its customer
area, *easyAPP*, holds your tax certificates, contract conditions, insurance cards
and invoices, but you can only get at them by logging in and downloading them one
by one. This small Docker container does that for you: it checks easyAPP on a
schedule and uploads every new document into your
[Paperless-ngx](https://docs.paperless-ngx.com) archive, tagged and ready to search.

```
 ┌──────────────┐   every 6 h (configurable)   ┌──────────────────────┐   REST API   ┌───────────────┐
 │ LALUX easyAPP│ ───────────────────────────▶ │ lalux-paperless-sync │ ───────────▶ │ Paperless-ngx │
 └──────────────┘   new documents only         └──────────────────────┘  tag + corr. └───────────────┘
```

> [!IMPORTANT]
> **This is an unofficial tool.** LALUX does not offer a public API. The container
> talks to the same API that the easyAPP web client uses, with your own login, and
> only reads your own documents. It is not affiliated with or endorsed by LALUX,
> and it may stop working whenever LALUX changes its app.

---

## Contents

- [What gets synced](#what-gets-synced)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Step-by-step setup](#step-by-step-setup)
  - [Step 1 — Create a Paperless API token](#step-1--create-a-paperless-api-token)
  - [Step 2 — Find the right Paperless URL](#step-2--find-the-right-paperless-url)
  - [Step 3 — Create the project folder](#step-3--create-the-project-folder)
  - [Step 4 — Write the compose file](#step-4--write-the-compose-file)
  - [Step 5 — Start the container](#step-5--start-the-container)
  - [Step 6 — Log in to LALUX once](#step-6--log-in-to-lalux-once)
  - [Step 7 — Check what LALUX offers](#step-7--check-what-lalux-offers)
  - [Step 8 — Run the first sync](#step-8--run-the-first-sync)
  - [Step 9 — Check the result in Paperless](#step-9--check-the-result-in-paperless)
- [Other ways to run it](#other-ways-to-run-it)
- [Configuration reference](#configuration-reference)
- [Commands](#commands)
- [Everyday operation](#everyday-operation)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [FAQ](#faq)
- [Development](#development)
- [License](#license)

---

## What gets synced

Everything easyAPP lets you download, from three places:

| Source (`LALUX_SOURCES`) | Where you see it in easyAPP        | Examples                                          |
|--------------------------|------------------------------------|---------------------------------------------------|
| `available`              | *My insurance → Documents*         | yearly tax certificates (*Steuerbescheinigung / certificat fiscal*) |
| `contracts`              | the detail page of each contract   | special conditions, insurance cards (green card)  |
| `invoices`               | *Invoices*                         | the PDF of each premium invoice                   |

Every document is uploaded **once**. The container remembers what it has already
synced, and before each upload it asks Paperless whether the exact same file is
already there, so you never get duplicates, even if you had uploaded some of these
documents by hand before.

## How it works

1. **One-time login.** easyAPP needs your password and a one-time code sent by SMS
   (or e-mail). You do this once, interactively, with the `login` command.
2. **Offline token.** During that login the container asks LALUX for an *offline
   token*. This is the same kind of token the easyAPP mobile app uses for its
   fingerprint/Face ID quick login. It is stored in `/data/state.json`. Your password
   is **not** stored.
3. **Unattended syncs.** From then on the container runs on its own. On every sync it
   renews the token, lists the documents, downloads the new ones and uploads them to
   Paperless with the tags and correspondent you chose.
4. **If the token is ever revoked**, e.g. because you changed your LALUX password,
   the log says `LALUX login needed` and the Docker health check turns
   *unhealthy*. Run `login` again and you are done.

## Requirements

- A LALUX / DKV Luxembourg customer account with access to
  [easyAPP](https://easyapphome.lalux.lu). If you can log in there, it works.
- The phone number (or e-mail) registered with LALUX, to receive the login code.
- A running **Paperless-ngx** instance, version 2.x or 3.x.
- **Docker** with the Compose plugin, on any machine that can reach both the
  internet and your Paperless server. The image is built for `amd64` and `arm64`,
  so a Raspberry Pi works too.

---

## Step-by-step setup

### Step 1 — Create a Paperless API token

The container uploads documents through the Paperless REST API and needs a token
for that.

1. Open Paperless in your browser and log in.
2. Click your user name at the top right and choose **My Profile**.
3. Under **API Auth Token**, click the ↻ button to generate a token.
4. Copy the token. You need it in step 4.

> [!TIP]
> Documents uploaded with this token belong to that user. If Paperless is shared
> with your family, use your own account, or a dedicated user with permission to add
> documents, tags and correspondents.

### Step 2 — Find the right Paperless URL

The container has to reach Paperless **directly**. Pick the first option that fits:

| Your setup                                                     | `PAPERLESS_URL`                    |
|----------------------------------------------------------------|------------------------------------|
| Paperless runs in the **same compose file / Docker network**    | `http://paperless:8000` (the service name) |
| Paperless has its own **LAN IP** (e.g. macvlan, another host)  | `http://192.168.1.50:8000`         |
| Paperless is only reachable through a **reverse proxy**        | `https://paperless.example.com`    |

> [!WARNING]
> If your reverse proxy puts a login page in front of Paperless (Authentik,
> Authelia, oauth2-proxy …), API calls with a token are usually blocked there.
> Use the internal address instead.

Quick test from the machine that will run the container. Replace the URL and the
token with yours:

```sh
curl -H "Authorization: Token YOUR_TOKEN" http://192.168.1.50:8000/api/tags/?page_size=1
```

You should get JSON back that starts with `{"count":`. A `401` means the token is
wrong. A timeout means the URL is not reachable from there.

### Step 3 — Create the project folder

```sh
mkdir -p ~/lalux-paperless-sync/data
cd ~/lalux-paperless-sync
```

The container runs as user id **1000**, not as root, and must be able to write to
`data/`:

```sh
sudo chown 1000:1000 data
```

### Step 4 — Write the compose file

Put your secrets in a file called `.env` next to the compose file:

```sh
cat > .env <<'EOF'
PAPERLESS_TOKEN=paste-your-paperless-token-here
LALUX_USERNAME=you@example.com
EOF
chmod 600 .env
```

`LALUX_USERNAME` is the e-mail address you use to log in to easyAPP.

Then create `docker-compose.yml`:

```yaml
services:
  lalux-paperless-sync:
    image: ghcr.io/racoon80/lalux-paperless-sync:latest
    container_name: lalux-paperless-sync
    restart: unless-stopped
    env_file: .env
    environment:
      PAPERLESS_URL: http://192.168.1.50:8000   # from step 2
      PAPERLESS_TAGS: LALUX                     # comma-separated, created if missing
      PAPERLESS_CORRESPONDENT: LALUX            # created if missing
      LALUX_OTP_TYPE: SMS                       # or EMAIL
      LALUX_LANGUAGE: fr                        # fr, de or en
      SYNC_INTERVAL: 86400                      # seconds; 86400 = once a day
      TZ: Europe/Luxembourg
    volumes:
      - ./data:/data
```

If Paperless runs in the **same** compose file, add this container as another
service and set `PAPERLESS_URL: http://<paperless-service-name>:8000`.

### Step 5 — Start the container

```sh
docker compose up -d
docker logs lalux-paperless-sync
```

Because nobody has logged in yet, the log shows:

```
INFO syncing every 86400s, state in /data
ERROR LALUX login needed (no stored token). Run: docker exec -it <container> lalux-paperless-sync login
```

That is expected. The container keeps running and waits for you.

### Step 6 — Log in to LALUX once

Have your phone ready, then run:

```sh
docker exec -it lalux-paperless-sync lalux-paperless-sync login
```

What happens:

```
LALUX password: ••••••••••          ← typed, not shown, not stored
LALUX sent a code by SMS.
Code: 123456                        ← the 6-digit code from the SMS
Logged in. Offline token stored — the sync can now run unattended.
```

- Don't forget the `-it`. Without it you can't type.
- The code is only valid for a few minutes, so enter it right away.
- To get the code by e-mail instead, set `LALUX_OTP_TYPE: EMAIL` in the compose
  file and run `docker compose up -d` before logging in.
- If you see `Invalid user credentials`, check the e-mail address in `.env` and your
  password by logging in at [easyapphome.lalux.lu](https://easyapphome.lalux.lu).
  Several failed attempts in a row may get your LALUX account locked for a while.

### Step 7 — Check what LALUX offers

Before anything is uploaded, you can look at what the container sees:

```sh
docker exec lalux-paperless-sync lalux-paperless-sync list
```

```
new     Steuerbescheinigungen / Steuerbescheinigung erhalten (Jahr) 2026 / Steuerbescheinigung Nicht-Lebensversicherung   [CONT#…]
new     easyPROTECT / VW GOLF 12345 / Versicherungskarte   [CONT#…]
new     Invoices / easyPROTECT P0000000 01.09.2026   [CONT#…]
…
```

`new` means not synced yet, `synced` means already done.

Optionally, do a dry run. It downloads everything and logs what it *would* upload,
without touching Paperless:

```sh
docker exec -e DRY_RUN=true lalux-paperless-sync lalux-paperless-sync sync
```

### Step 8 — Run the first sync

Don't wait for the next scheduled run. Start one now:

```sh
docker exec lalux-paperless-sync lalux-paperless-sync sync
```

```
INFO LALUX offers 14 documents, 14 new
INFO Clients..…pdf -> Paperless: SUCCESS {"document_id": 1122}
…
```

Each upload waits until Paperless has processed the file, so with many documents and
OCR this takes a few minutes.

> [!TIP]
> Only want documents that appear **from now on**, not the old ones? Set
> `SYNC_EXISTING: "false"` **before** the first sync. Everything that is there at
> that moment is then marked as done without being uploaded.

### Step 9 — Check the result in Paperless

In Paperless, filter on the tag **LALUX**. Each document has:

- **Title**: what easyAPP calls it, e.g. `VW GOLF 12345 – Versicherungskarte`
- **Tag**: `LALUX`, or whatever you set in `PAPERLESS_TAGS`
- **Correspondent**: `LALUX`
- **Date**: detected by Paperless from the document content, as usual

Workflows, matching rules or AI tagging (e.g. paperless-gpt) run on these documents
just like on any other upload.

**That's it.** From now on the container syncs by itself every `SYNC_INTERVAL`
seconds.

---

## Other ways to run it

### `docker run`

```sh
docker run -d --name lalux-paperless-sync --restart unless-stopped \
  -e PAPERLESS_URL=http://192.168.1.50:8000 \
  -e PAPERLESS_TOKEN=your-token \
  -e LALUX_USERNAME=you@example.com \
  -e SYNC_INTERVAL=86400 \
  -e TZ=Europe/Luxembourg \
  -v /path/to/data:/data \
  ghcr.io/racoon80/lalux-paperless-sync:latest

docker exec -it lalux-paperless-sync lalux-paperless-sync login
```

### Unraid

Use the **Compose Manager** plugin:

1. *Docker → Compose → Add New Stack*, name it `lalux-paperless-sync`.
2. Paste the compose file from [step 4](#step-4--write-the-compose-file) and set the
   volume to `/mnt/user/appdata/lalux-paperless-sync:/data`.
3. Put `PAPERLESS_TOKEN` and `LALUX_USERNAME` in the stack's *env file*.
4. On the Unraid console, run
   `mkdir -p /mnt/user/appdata/lalux-paperless-sync && chown 1000:1000 /mnt/user/appdata/lalux-paperless-sync`.
5. *Compose Up*, then do [step 6](#step-6--log-in-to-lalux-once) from the Unraid
   terminal.

If Paperless has its own IP on a custom (macvlan) network such as `br0`, the
container can join that network too:

```yaml
    networks:
      br0:
        ipv4_address: 192.168.1.51   # a free address

networks:
  br0:
    external: true
```

### Without Docker

Python 3.11 or newer:

```sh
pip install git+https://github.com/Racoon80/lalux-paperless-sync
export STATE_DIR=~/.local/share/lalux-paperless-sync
export PAPERLESS_URL=http://192.168.1.50:8000 PAPERLESS_TOKEN=… LALUX_USERNAME=you@example.com
lalux-paperless-sync login
lalux-paperless-sync sync        # e.g. from cron once a day
```

---

## Configuration reference

All settings are environment variables.

| Variable                  | Default                        | Description |
|---------------------------|--------------------------------|-------------|
| `PAPERLESS_URL`           | —                              | **Required.** Base URL of Paperless, without `/api`. |
| `PAPERLESS_TOKEN`         | —                              | **Required.** Paperless API token (step 1). |
| `PAPERLESS_TAGS`          | `LALUX`                        | Comma-separated tag names. Missing tags are created. Empty = no tags. |
| `PAPERLESS_CORRESPONDENT` | `LALUX`                        | Correspondent name, created if missing. Empty = none. |
| `PAPERLESS_DOCUMENT_TYPE` | *(none)*                       | Optional document type name, created if missing. |
| `LALUX_USERNAME`          | —                              | easyAPP login e-mail. Only used by `login`; asked for if not set. |
| `LALUX_PASSWORD`          | —                              | Only used by `login`; asked for (hidden) if not set. Better not set permanently. |
| `LALUX_OTP_TYPE`          | `SMS`                          | Where LALUX sends the login code: `SMS` or `EMAIL`. |
| `LALUX_LANGUAGE`          | `fr`                           | Language sent to LALUX: `fr`, `de` or `en`. LALUX may use your profile language anyway. |
| `LALUX_SOURCES`           | `available,contracts,invoices` | Which [document sources](#what-gets-synced) to sync. |
| `SYNC_INTERVAL`           | `21600`                        | Seconds between syncs. `21600` = 6 h, `86400` = 24 h. |
| `SYNC_EXISTING`           | `true`                         | `false` = on the very first sync, mark existing documents as done without uploading. |
| `DRY_RUN`                 | `false`                        | `true` = download and log, upload nothing, change nothing in Paperless. |
| `STATE_DIR`               | `/data`                        | Where `state.json` and `status.json` are kept. |
| `LOG_LEVEL`               | `INFO`                         | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `TZ`                      | `UTC`                          | Time zone for the log timestamps. |

## Commands

Run them with `docker exec [-it] lalux-paperless-sync lalux-paperless-sync <command>`:

| Command  | What it does |
|----------|--------------|
| `login`  | Interactive one-time login with password and SMS/e-mail code. Needs `-it`. |
| `run`    | Sync every `SYNC_INTERVAL` seconds. This is what the container runs by default. |
| `sync`   | One sync right now, then exit. |
| `list`   | List the documents LALUX offers and whether each one is synced already. |
| `health` | Exit code 0 if the last sync succeeded recently. Used by the Docker health check. |

## Everyday operation

**Is it working?**

```sh
docker ps --filter name=lalux-paperless-sync     # STATUS shows (healthy)
docker logs --tail 20 lalux-paperless-sync
cat data/status.json                             # last attempt, last success, counts
```

**Update to the latest version**

```sh
docker compose pull && docker compose up -d
```

The login survives updates because it lives in `data/`.

**Sync a document again** that you deleted in Paperless: remove its entry from the
`synced` section of `data/state.json`, or delete the whole section to re-check
everything. Files that are still in Paperless are recognised by checksum and not
uploaded twice.

**Log out / start over**: stop the container and delete `data/state.json`. You can
also end the session on LALUX's side by changing your easyAPP password.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `LALUX login needed (no stored token)` | Nobody has logged in yet. Do [step 6](#step-6--log-in-to-lalux-once). |
| `LALUX login needed (… invalid_grant …)` | LALUX no longer accepts the stored token (password changed, session ended, or the container didn't run for a long time). Run `login` again. |
| `invalid_grant Invalid user credentials` during `login` | Wrong e-mail or password. Test them at [easyapphome.lalux.lu](https://easyapphome.lalux.lu) first; too many attempts can lock the account. |
| No SMS arrives | Check the phone number in easyAPP, wait a minute, or use `LALUX_OTP_TYPE=EMAIL`. |
| `PermissionError: … /data/state.json` | The data folder is not writable by uid 1000: `sudo chown -R 1000:1000 data`. |
| `Paperless rejected the token (401/403)` | Wrong token, or a reverse proxy with a login page in front of Paperless. See [step 2](#step-2--find-the-right-paperless-url). |
| `Connection refused` / timeout to Paperless | The container can't reach `PAPERLESS_URL`. Test with `docker exec lalux-paperless-sync python -c "import urllib.request;print(urllib.request.urlopen('http://…/api/').status)"`. On macvlan setups, a container on the default bridge often can't reach macvlan IPs on the same host; put it on the same network. |
| `download failed … 401` / `404` | LALUX changed something, or the document is only available on request in easyAPP. Please open an issue with the log (remove personal data). |
| Health check `unhealthy` | The last sync failed, or no sync succeeded for more than 2 × `SYNC_INTERVAL`. Look at `docker logs`. |

For more detail, set `LOG_LEVEL=DEBUG`.

## Security

- **Your password is never stored.** It is used once during `login` and then
  forgotten.
- **`data/state.json` holds the offline token.** Anyone who has this file can read
  your easyAPP documents until the token is revoked. It is written with mode `0600`.
  Keep the folder private and don't put it in public backups or Git.
- The token only has the rights of the easyAPP app. The container only **reads**:
  it lists and downloads documents and never changes anything in your LALUX account.
- Keep `.env` (Paperless token) out of Git as well; the included `.gitignore`
  already does.
- To revoke access immediately, change your easyAPP password.

## FAQ

**Is this allowed?**
It uses your own credentials to download your own documents, the same way the
official web client does, and at a very low rate (by default once every few hours).
Still, it is unofficial. Check LALUX's terms of use if you want to be sure, and use it
at your own risk.

**Does it work for DKV Luxembourg customers?**
DKV customers use the same easyAPP login, so it should work, but this has not been
tested. Feedback welcome.

**Can it also fetch documents from MyGuichet.lu?**
No. MyGuichet has no API for private persons and requires LuxTrust/eID for every
login, so it cannot be automated reliably.

**Why not the Paperless mail consumer or a consume folder?**
LALUX doesn't e-mail the documents, it only sends notifications. The API upload also
lets the container set title, tags and correspondent directly.

## Development

```sh
git clone https://github.com/Racoon80/lalux-paperless-sync
cd lalux-paperless-sync
python -m venv .venv && . .venv/bin/activate
pip install -e .
STATE_DIR=./data lalux-paperless-sync login
STATE_DIR=./data DRY_RUN=true lalux-paperless-sync sync
docker build -t lalux-paperless-sync .
```

Layout:

```
lalux_paperless_sync/
  lalux.py       easyAPP API client: login, token refresh, listing, download
  paperless.py   Paperless-ngx client: tags/correspondents, dedupe, upload
  __main__.py    CLI commands, state handling, sync loop
```

Every push to `main` builds and publishes the multi-arch image to
`ghcr.io/racoon80/lalux-paperless-sync` via GitHub Actions. Tags `v*` also publish
versioned images.

Issues and pull requests are welcome. Please remove personal data such as contract
numbers, licence plates and names from logs before posting them.

## License

[MIT](LICENSE). Not affiliated with LALUX Assurances or DKV Luxembourg.
