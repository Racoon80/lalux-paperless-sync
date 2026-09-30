"""lalux-paperless-sync: copy documents from LALUX easyAPP into Paperless-ngx.

  login     one-time interactive login (password + OTP), stores an offline token
  sync      run one sync and exit
  run       sync every SYNC_INTERVAL seconds (default, used by the container)
  list      show the documents LALUX currently offers, without uploading
  health    exit 0 if the last sync succeeded recently (Docker HEALTHCHECK)
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .lalux import LaluxClient, LaluxError, ReloginRequired
from .paperless import PaperlessClient

log = logging.getLogger("lalux-paperless-sync")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    return default if value is None else value.lower() in ("1", "true", "yes", "on")


STATE_DIR = Path(env("STATE_DIR", "/data"))
STATE_FILE = STATE_DIR / "state.json"
STATUS_FILE = STATE_DIR / "status.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"refresh_token": None, "synced": {}}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def lalux_client() -> LaluxClient:
    return LaluxClient(language=env("LALUX_LANGUAGE", "fr"))


def sources() -> set[str]:
    raw = env("LALUX_SOURCES", "available,contracts,invoices") or ""
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


# --- commands ---------------------------------------------------------------

def cmd_login() -> int:
    username = env("LALUX_USERNAME") or input("LALUX username (e-mail): ").strip()
    password = env("LALUX_PASSWORD") or getpass.getpass("LALUX password: ")
    otp_type = (env("LALUX_OTP_TYPE", "SMS") or "SMS").upper()

    client = lalux_client()
    session_token = client.start_login(username, password, otp_type)
    print(f"LALUX sent a code by {otp_type}.")
    otp = env("LALUX_OTP") or input("Code: ").strip()
    tokens = client.finish_login(session_token, otp, otp_type)

    state = load_state()
    state["refresh_token"] = tokens.refresh_token
    state["logged_in_at"] = now()
    save_json(STATE_FILE, state)

    if "offline_access" in tokens.scope.split():
        print("Logged in. Offline token stored — the sync can now run unattended.")
    else:
        print("Logged in, but LALUX did not grant offline_access; the token will "
              "expire with the session and login will be needed again.")
    return 0


def authenticate(client: LaluxClient, state: dict) -> None:
    if not state.get("refresh_token"):
        raise ReloginRequired("no stored token")

    def persist(refresh_token: str) -> None:
        state["refresh_token"] = refresh_token
        save_json(STATE_FILE, state)

    client.on_refresh = persist
    client.refresh(state["refresh_token"])


def cmd_list() -> int:
    state = load_state()
    client = lalux_client()
    authenticate(client, state)
    for doc in client.list_documents(sources()):
        mark = "synced " if doc.id in state["synced"] else "new    "
        print(f"{mark} {doc.category} / {doc.subcategory} / {doc.label}   [{doc.id}]")
    return 0


def sync_once() -> dict:
    state = load_state()
    client = lalux_client()
    authenticate(client, state)

    docs = client.list_documents(sources())
    new = [d for d in docs if d.id not in state["synced"]]
    log.info("LALUX offers %d documents, %d new", len(docs), len(new))

    dry_run = env_bool("DRY_RUN")
    if not state.get("first_sync_done") and not env_bool("SYNC_EXISTING", True):
        for d in new:
            state["synced"][d.id] = {"label": d.label, "skipped": True, "at": now()}
        state["first_sync_done"] = True
        save_json(STATE_FILE, state)
        log.info("SYNC_EXISTING=false: marked %d existing documents as done", len(new))
        return {"offered": len(docs), "uploaded": 0}

    if not new:
        state["first_sync_done"] = True
        save_json(STATE_FILE, state)
        return {"offered": len(docs), "uploaded": 0}

    if not dry_run:
        paperless = PaperlessClient(env("PAPERLESS_URL") or "", env("PAPERLESS_TOKEN") or "")
        paperless.check()
        tag_ids = [paperless.ensure("tags", t.strip())
                   for t in (env("PAPERLESS_TAGS", "LALUX") or "").split(",") if t.strip()]
        corr = env("PAPERLESS_CORRESPONDENT", "LALUX")
        corr_id = paperless.ensure("correspondents", corr) if corr else None
        dtype = env("PAPERLESS_DOCUMENT_TYPE")
        dtype_id = paperless.ensure("document_types", dtype) if dtype else None

    uploaded = failed = 0
    for doc in new:
        title = " – ".join(p for p in (doc.subcategory, doc.label) if p) or doc.label
        try:
            filename, content = client.download(doc)
        except Exception as e:
            log.warning("download failed for %r: %s", title, e)
            failed += 1
            continue
        if dry_run:
            log.info("[dry-run] would upload %s (%d bytes) as %r", filename, len(content), title)
            continue
        existing = paperless.find_by_content(content)
        if existing:
            log.info("%s is already in Paperless as document %d", filename, existing)
            state["synced"][doc.id] = {"label": doc.label, "filename": filename,
                                       "paperless_document": existing, "at": now()}
            save_json(STATE_FILE, state)
            continue
        task_id = paperless.upload(filename, content, title, tag_ids, corr_id, dtype_id)
        status, message = paperless.wait_for_task(task_id)
        log.info("%s -> Paperless: %s %s", filename, status, message)
        # Paperless has the file once it accepted it; a duplicate is already
        # there too. Only a real failure is retried on the next run.
        if status in ("FAILURE", "REVOKED") and "duplicate" not in message.lower():
            failed += 1
            continue
        state["synced"][doc.id] = {
            "label": doc.label,
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "paperless_task": task_id,
            "paperless_status": status,
            "at": now(),
        }
        state["first_sync_done"] = True
        save_json(STATE_FILE, state)
        uploaded += 1
    return {"offered": len(docs), "uploaded": uploaded, "failed": failed}


def cmd_sync() -> int:
    status = {"last_attempt": now()}
    try:
        result = sync_once()
        status.update(ok=True, last_success=now(), **result)
        code = 0
    except ReloginRequired as e:
        log.error("LALUX login needed (%s). Run: docker exec -it <container> "
                  "lalux-paperless-sync login", e)
        status.update(ok=False, error=f"relogin required: {e}")
        code = 2
    except (LaluxError, Exception) as e:  # keep the daemon alive on any error
        log.exception("sync failed: %s", e)
        status.update(ok=False, error=str(e))
        code = 1
    previous = json.loads(STATUS_FILE.read_text()) if STATUS_FILE.exists() else {}
    if not status.get("ok") and previous.get("last_success"):
        status["last_success"] = previous["last_success"]
    save_json(STATUS_FILE, status)
    return code


def cmd_run() -> int:
    interval = int(env("SYNC_INTERVAL", "21600") or 21600)
    log.info("syncing every %ds, state in %s", interval, STATE_DIR)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        cmd_sync()
        time.sleep(interval)


def cmd_health() -> int:
    if not STATUS_FILE.exists():
        return 0  # still starting up
    status = json.loads(STATUS_FILE.read_text())
    if not status.get("ok"):
        return 1
    interval = int(env("SYNC_INTERVAL", "21600") or 21600)
    last = datetime.fromisoformat(status["last_success"])
    return 0 if (datetime.now(timezone.utc) - last).total_seconds() < 2 * interval + 600 else 1


COMMANDS = {"login": cmd_login, "sync": cmd_sync, "run": cmd_run,
            "list": cmd_list, "health": cmd_health}


def main() -> None:
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command not in COMMANDS:
        print(__doc__)
        sys.exit(64)
    try:
        sys.exit(COMMANDS[command]())
    except ReloginRequired as e:
        print(f"LALUX login needed: {e}. Run the `login` command first.", file=sys.stderr)
        sys.exit(2)
    except LaluxError as e:
        print(f"LALUX error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
