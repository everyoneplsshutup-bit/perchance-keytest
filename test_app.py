"""Perchance Turnstile / key-mint test.

Runs a real headless Chromium against the Perchance generator page on THIS
container's own IP (or through the residential proxy in PROXY_URL when set),
watches the Turnstile gate, and — if a userKey is ever minted — immediately
tests it server-side from this same container/network via the real generate
API (POST image-generation.perchance.org/api/generate).

Set PROXY_URL as a container env var (never in the repo) to route the
browser AND the server-side test through a proxy, e.g. a residential proxy.
The result is served as JSON at GET /. NO key, cookie, token or proxy
credential is ever written to disk or to the result: only booleans, counts,
and (on success) the returned imageId, which is the proof that Perchance
generation worked from this IP.

Endpoints:
  GET  /       -> latest test result JSON (or {"status": "not_run_yet"})
  GET  /health -> {"status": "ok"}
  POST /run    -> run the browser test once (409 if a run is already going)
"""
import json
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

RESULT_FILE = Path(__file__).resolve().parent / "result.json"
GENERATOR_URL = "https://perchance.org/ai-text-to-image-generator"
GENERATE_BASE = "https://image-generation.perchance.org/api"

app = FastAPI(title="Perchance key-mint test", version="1.0.0")
_lock = threading.Lock()
_result = {"status": "not_run_yet"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _save(result):
    global _result
    _result = result
    try:
        RESULT_FILE.write_text(json.dumps(result, indent=2))
    except Exception:
        pass


def _proxy_config():
    """Return (httpx_proxy_url, playwright_proxy_dict) from PROXY_URL env,
    or (None, None) when no proxy is configured. The URL itself is never
    logged or put in any result."""
    raw = (os.environ.get("PROXY_URL") or "").strip()
    if not raw:
        return None, None
    u = urlparse(raw)
    pw = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        pw["username"] = u.username
    if u.password:
        pw["password"] = u.password
    return raw, pw


def _container_ip_info():
    info = {}
    proxy_url, _ = _proxy_config()
    info["proxy_used"] = bool(proxy_url)
    try:
        with httpx.Client(timeout=15, proxy=proxy_url) as c:
            info["ip"] = c.get("https://api.ipify.org").text.strip()
            r = c.get(f"http://ip-api.com/json/{info['ip']}?fields=status,org,as,isp")
            d = r.json()
            info["asn"] = d.get("as")
            info["org"] = d.get("org")
            info["isp"] = d.get("isp")
    except Exception as e:
        info["ip_error"] = str(e)[:200]
    return info


def _server_side_generate(key):
    """Test a browser-minted key from THIS container's network. Returns a
    short dict: outcome is 'imageId' (SUCCESS), 'invalid_key', or 'error'."""
    url = (
        f"{GENERATE_BASE}/generate?userKey={key}"
        f"&requestId={random.random()}"
        f"&adAccessCode=&__cacheBust={random.random()}&bdf=0.4804946457410627"
    )
    body = {
        "generatorName": "ai-image-generator",
        "channel": "ai-text-to-image-generator",
        "subChannel": "public",
        "prompt": "a red apple on a wooden table",
        "negativePrompt": "",
        "seed": -1,
        "resolution": "768x768",
        "guidanceScale": 7.0,
    }
    try:
        proxy_url, _ = _proxy_config()
        with httpx.Client(timeout=120, proxy=proxy_url) as c:
            r = c.post(url, json=body,
                       headers={"Content-Type": "text/plain;charset=UTF-8",
                                "User-Agent": "Mozilla/5.0"})
            try:
                data = r.json()
            except Exception:
                return {"outcome": "error", "detail": f"non-JSON HTTP {r.status_code}"}
            if isinstance(data, dict) and "imageId" in data:
                image_id = data["imageId"]
                dl = c.get(f"{GENERATE_BASE}/downloadTemporaryImage?imageId={image_id}",
                           timeout=120)
                return {"outcome": "imageId", "imageId": image_id,
                        "download_http": dl.status_code,
                        "download_bytes": len(dl.content) if dl.status_code == 200 else 0}
            text = json.dumps(data)[:300] if isinstance(data, dict) else str(data)[:300]
            if "invalid_key" in text:
                return {"outcome": "invalid_key", "detail": text}
            return {"outcome": "error", "detail": text}
    except Exception as e:
        return {"outcome": "error", "detail": str(e)[:300]}


def run_browser_test():
    """One headless-Chromium run. Returns the result dict (no secrets)."""
    from playwright.sync_api import sync_playwright

    result = {"status": "running", "started_at": _now()}
    result.update(_container_ip_info())
    counts = {"verifyUser": 0, "turnstileFail": 0, "generate": 0, "queuePosition": 0}
    timeline = []
    key_holder = {"key": None}

    def note(msg):
        timeline.append(f"{_now()[11:19]} {msg}")

    def on_request(req):
        u = req.url
        if "verifyUser" in u:
            counts["verifyUser"] += 1
        if "turnstileClientFail" in u:
            counts["turnstileFail"] += 1
        if "image-generation.perchance.org/api/generate" in u:
            counts["generate"] += 1
        if "getUserQueuePosition" in u:
            counts["queuePosition"] += 1
        if key_holder["key"] is None and ("api/generate" in u or "getUserQueuePosition" in u):
            q = parse_qs(urlparse(u).query)
            k = (q.get("userKey") or [None])[0]
            if k and len(k) >= 32:
                key_holder["key"] = k  # in-memory only, never persisted
                note("userKey observed in a browser request")

    try:
        with sync_playwright() as p:
            _, pw_proxy = _proxy_config()
            launch_kwargs = {"headless": True, "args": [
                "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                "--single-process", "--disable-extensions",
                "--disable-background-networking",
            ]}
            if pw_proxy:
                launch_kwargs["proxy"] = pw_proxy
                note("browser using PROXY_URL (residential proxy)")
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("request", on_request)
            note("navigating to generator page")
            try:
                page.goto(GENERATOR_URL, timeout=60000, wait_until="domcontentloaded")
            except Exception as e:
                note(f"goto failed: {str(e)[:150]}")
            time.sleep(10)
            result["page_title"] = page.title()[:200]
            body_text = ""
            try:
                body_text = page.evaluate("() => document.body.innerText.slice(0, 2000)")
            except Exception:
                pass
            result["challenge_text_seen"] = (
                "Anti-bot verification failed" in body_text
                or "Just a moment" in body_text
                or "verify you are human" in body_text.lower()
            )
            note(f"page title: {result['page_title']!r}; "
                 f"challenge_text={result['challenge_text_seen']}; "
                 f"verifyUser={counts['verifyUser']} turnstileFail={counts['turnstileFail']}")
            # Try to actually generate: fill the visible prompt box and click Generate.
            clicked = False
            try:
                ta = page.locator("textarea:visible").first
                ta.fill("a red apple on a wooden table", timeout=15000)
                btn = page.get_by_role("button", name="Generate").first
                if btn.count() == 0:
                    btn = page.locator("button:has-text('Generate')").first
                btn.click(timeout=15000)
                clicked = True
                note("clicked Generate")
            except Exception as e:
                note(f"generate click failed (page may be gated): {str(e)[:150]}")
            result["generate_clicked"] = clicked
            # Observe up to 180s for a generate request / key.
            for _ in range(36):
                if counts["generate"] > 0 or key_holder["key"]:
                    break
                time.sleep(5)
            note(f"after observe: verifyUser={counts['verifyUser']} "
                 f"turnstileFail={counts['turnstileFail']} "
                 f"generate={counts['generate']} queue={counts['queuePosition']} "
                 f"key_seen={key_holder['key'] is not None}")
            browser.close()
    except Exception as e:
        note(f"browser error: {str(e)[:300]}")
        result["browser_error"] = str(e)[:300]

    result["counts"] = counts
    result["key_seen"] = key_holder["key"] is not None
    result["timeline"] = timeline
    if key_holder["key"]:
        note("testing minted key server-side from this container")
        result["server_side_test"] = _server_side_generate(key_holder["key"])
        key_holder["key"] = None  # drop it immediately after use
    else:
        result["server_side_test"] = {"outcome": "not_attempted",
                                      "detail": "no key was minted in the browser"}
    result["status"] = "done"
    result["finished_at"] = _now()
    _save(result)
    return result


def _run_in_background():
    if not _lock.acquire(blocking=False):
        return
    try:
        run_browser_test()
    finally:
        _lock.release()


@app.on_event("startup")
def _startup():
    # Run the test once automatically shortly after deploy, so the container
    # produces a result even if nobody calls /run (it sleeps after 15 min idle).
    threading.Thread(target=lambda: (time.sleep(15), _run_in_background()),
                     daemon=True).start()


@app.get("/")
def root():
    if RESULT_FILE.exists() and _result.get("status") == "not_run_yet":
        try:
            return json.loads(RESULT_FILE.read_text())
        except Exception:
            pass
    return _result


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run():
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "a test run is already in progress")
    threading.Thread(target=lambda: (run_browser_test(), _lock.release()),
                     daemon=True).start()
    return JSONResponse({"status": "started"}, status_code=202)
