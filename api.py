import os
import re
import json
import time
import random
import asyncio
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from curl_cffi import requests as cffi_requests
from pydantic import BaseModel
from urllib.parse import urlparse, urlunparse

app = FastAPI()

PROXY_FILE = "proxies.txt"
PROXIES: List[str] = []
PROXY_FAILURES: Dict[str, int] = {}
MAX_PROXY_FAILURES = 4  # Raised so a noisy proxy doesn't exhaust the pool too fast
FALLBACK_STATS = {"narrow_hit": 0, "fallback_fired": 0, "fallback_hit": 0, "total_miss": 0}

# Set PROXY_ENABLED=false in HF Space env vars to skip proxy entirely and go
# direct on every request.  Useful when the proxy port is firewalled by the
# hosting provider (e.g. port 823 is blocked from Hugging Face).
_PROXY_ENABLED = os.environ.get("PROXY_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")
if not _PROXY_ENABLED:
    print("[PROXY] PROXY_ENABLED=false — all requests will go direct (no proxy).")

# -----------------------------------------------------------------------
# Two-tier concurrency control:
#
#  _PRIORITY_SEMAPHORE (3 slots):
#    Used by /check/comment, /check/post, /proxy/json — the endpoints
#    called by the automated liveness / hold-end checker.  These are
#    NEVER starved, even during a heavy Inspector bulk run.
#
#  _BULK_SEMAPHORE (3 slots):
#    Slow & Steady bulk fetches to mimic human browsing behavior.
#    Limits concurrency to 3 slots for bulk requests.
# -----------------------------------------------------------------------
_PRIORITY_SEMAPHORE = asyncio.Semaphore(3)
_BULK_SEMAPHORE     = asyncio.Semaphore(3)
_AUTHOR_SEMAPHORE   = asyncio.Semaphore(2)

IS_SINGLE_ROTATING_GATEWAY = False
PROXY_INDEX = 0

# In-memory caches (Key -> (Value, ExpiryTime))
SHORTLINK_CACHE: Dict[str, Any] = {}
POST_CACHE: Dict[str, Any] = {}
COMMENT_CACHE: Dict[str, Any] = {}
AUTHOR_CACHE: Dict[str, Any] = {}

def get_from_cache(cache: dict, key: str) -> Optional[Any]:
    if key in cache:
        val, expiry = cache[key]
        if time.time() < expiry:
            return val
        else:
            del cache[key]
    return None

def set_in_cache(cache: dict, key: str, value: Any, ttl: float = 3600.0):
    cache[key] = (value, time.time() + ttl)

class StealthResponse:
    def __init__(self, status_code: int, text: str, headers: Dict[str, str], url: str):
        self.status_code = status_code
        self.text = text
        self.headers = headers
        self.url = url

    def json(self) -> Any:
        return json.loads(self.text)

async def read_stream_limit(resp, limit_bytes: int = 8192) -> bytes:
    chunks = []
    bytes_read = 0
    try:
        async for chunk in resp.aiter_content(chunk_size=1024):
            chunks.append(chunk)
            bytes_read += len(chunk)
            if bytes_read >= limit_bytes:
                break
    finally:
        await resp.aclose()
    return b"".join(chunks)

# -----------------------------------------------------------------------
# Proxy circuit breaker
#
# When every proxy attempt in a single stealth_fetch call times out we
# increment _PROXY_CONSECUTIVE_TIMEOUTS.  After CIRCUIT_BREAKER_THRESHOLD
# consecutive all-timeout cycles the circuit "opens": subsequent calls
# skip the proxy phase entirely and go straight to the direct fallback.
# The circuit resets after CIRCUIT_RESET_SECS seconds so we keep retrying
# the proxy periodically rather than giving up forever.
#
# This turns the 74-second worst-case (6 × 10s + 14s direct) into
# ~14 seconds (direct only) whenever the DataImpulse gateway is down.
# -----------------------------------------------------------------------
CIRCUIT_BREAKER_THRESHOLD = 2   # consecutive all-timeout cycles before opening
CIRCUIT_RESET_SECS = 300        # 5 minutes
_PROXY_CONSECUTIVE_TIMEOUTS = 0
_PROXY_CIRCUIT_OPEN_UNTIL   = 0.0  # epoch seconds; 0 = closed

# Maximum bytes to read per Reddit response (400 KB).
# Reddit JSON is gzip-compressed, so 400 KB covers every realistic payload
# while protecting against accidentally downloading a huge thread.
MAX_RESPONSE_BYTES = 400 * 1024


def _is_circuit_open() -> bool:
    """Return True if the proxy circuit breaker is open (skip proxy)."""
    global _PROXY_CIRCUIT_OPEN_UNTIL
    if _PROXY_CIRCUIT_OPEN_UNTIL and time.time() < _PROXY_CIRCUIT_OPEN_UNTIL:
        return True
    if _PROXY_CIRCUIT_OPEN_UNTIL:
        # Timer expired — reset so we try proxy again
        _PROXY_CIRCUIT_OPEN_UNTIL = 0.0
    return False


def _record_proxy_all_timeout() -> None:
    """Called when every proxy attempt in one stealth_fetch cycle timed out."""
    global _PROXY_CONSECUTIVE_TIMEOUTS, _PROXY_CIRCUIT_OPEN_UNTIL
    _PROXY_CONSECUTIVE_TIMEOUTS += 1
    if _PROXY_CONSECUTIVE_TIMEOUTS >= CIRCUIT_BREAKER_THRESHOLD:
        _PROXY_CIRCUIT_OPEN_UNTIL = time.time() + CIRCUIT_RESET_SECS
        print(f"[CIRCUIT] Proxy circuit OPEN — skipping proxy for {CIRCUIT_RESET_SECS}s "
              f"({_PROXY_CONSECUTIVE_TIMEOUTS} consecutive all-timeout cycles)")
        _PROXY_CONSECUTIVE_TIMEOUTS = 0


def _record_proxy_success() -> None:
    """Called on a successful proxy fetch — resets the circuit breaker counter."""
    global _PROXY_CONSECUTIVE_TIMEOUTS
    _PROXY_CONSECUTIVE_TIMEOUTS = 0


def _parse_proxy_lines(lines: list) -> list:
    """Parse raw proxy strings into normalised URLs."""
    formatted = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        # If the user provides a full URL with scheme, keep it
        if line.startswith("http://") or line.startswith("https://") or line.startswith("socks5://") or line.startswith("socks5h://"):
            formatted.append(line)
            continue
            
        # Otherwise, assume http:// and format
        if "@" in line:
            formatted.append(f"http://{line}")
            continue
        parts = line.split(":")
        if len(parts) >= 4:
            host, port, user, pw = parts[:4]
            formatted.append(f"http://{user}:{pw}@{host}:{port}")
        else:
            formatted.append(f"http://{line}")
    return formatted


def load_proxies():
    """Load proxies from proxies.txt and/or PROXY_LIST_URL env var (one proxy per line)."""
    global PROXIES, IS_SINGLE_ROTATING_GATEWAY
    lines = []

    # Source 1: local proxies.txt
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            lines += [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

    # Source 2: PROXY_LIST_URL — fetches one proxy per line from a remote URL
    proxy_list_url = os.environ.get("PROXY_LIST_URL", "").strip()
    if proxy_list_url:
        try:
            import urllib.request
            with urllib.request.urlopen(proxy_list_url, timeout=10) as resp:
                remote_lines = resp.read().decode("utf-8").splitlines()
            lines += [l.strip() for l in remote_lines if l.strip() and not l.strip().startswith("#")]
            print(f"[PROXY] Fetched {len(remote_lines)} lines from PROXY_LIST_URL.")
        except Exception as e:
            print(f"[PROXY] WARNING: Could not fetch PROXY_LIST_URL: {e}")

    # Source 3: PROXY_STRING — a direct rotating proxy string (e.g., DataImpulse gateway)
    proxy_string = os.environ.get("PROXY_STRING", "").strip()
    if proxy_string:
        lines.append(proxy_string)
        if "gateway.dataimpulse.com" in proxy_string or "dataimpulse.com:823" in proxy_string or "gw.dataimpulse.com" in proxy_string:
            IS_SINGLE_ROTATING_GATEWAY = True
            print("[PROXY] Detected single rotating gateway (DataImpulse) in PROXY_STRING. Rotation disabled.")
        else:
            print("[PROXY] Loaded single rotating gateway from PROXY_STRING.")

    PROXIES = _parse_proxy_lines(lines)
    
    # Enable single gateway mode if we only have 1 proxy or if any proxy is DataImpulse
    if len(PROXIES) == 1 or any("dataimpulse" in p or ":823" in p for p in PROXIES):
        IS_SINGLE_ROTATING_GATEWAY = True

    if not IS_SINGLE_ROTATING_GATEWAY and len(PROXIES) > 1:
        random.shuffle(PROXIES)
    print(f"Loaded {len(PROXIES)} proxies total. IS_SINGLE_ROTATING_GATEWAY={IS_SINGLE_ROTATING_GATEWAY}")

load_proxies()


def get_healthy_proxy() -> Optional[str]:
    global PROXY_INDEX
    if not PROXIES:
        return None
    if IS_SINGLE_ROTATING_GATEWAY:
        # Always use the gateway, DataImpulse handles rotation internally per connection
        return PROXIES[-1]
    healthy = [p for p in PROXIES if PROXY_FAILURES.get(p, 0) < MAX_PROXY_FAILURES]
    if not healthy:
        # If all fail, reset failures and try again
        PROXY_FAILURES.clear()
        healthy = PROXIES
    proxy = healthy[PROXY_INDEX % len(healthy)]
    PROXY_INDEX += 1
    return proxy

async def stealth_fetch(url: str, method: str = "GET", allow_redirects: bool = True) -> cffi_requests.Response:
    """
    Fetch a URL with rotating residential proxies.

    Strategy (proxy-first):
      - Skip the direct (no-proxy) attempt entirely; Hugging Face egress IPs are
        permanently blocked by Reddit / Cloudflare.
      - Try up to MAX_RETRIES proxy attempts.
      - Cycle through Chrome131, Firefox, and Safari to bypass browser-specific blocks.
      - Direct fallback is only used as a last resort, also cycling through browser signatures.
    """
    async with _PRIORITY_SEMAPHORE:
        MAX_RETRIES = 3
        PROXY_TIMEOUT = 12.0
        last_err = None
        all_timed_out = True

        # Phase 1 — Proxy-first attempts (skip if circuit breaker is open)
        if _PROXY_ENABLED and PROXIES and not _is_circuit_open():
            for attempt in range(MAX_RETRIES):
                if PROXIES and sum(1 for p in PROXIES if PROXY_FAILURES.get(p, 0) >= MAX_PROXY_FAILURES) > len(PROXIES) // 2:
                    print(f"[PROXY] Majority of proxies unhealthy — resetting failure counters (attempt {attempt+1})")
                    PROXY_FAILURES.clear()

                proxy = get_healthy_proxy()
                proxies_config = {"http": proxy, "https": proxy} if proxy else None

                if attempt > 0 and len(PROXIES) > 1:
                    sleep_s = min(0.3 * (2 ** (attempt - 1)), 2.0) + random.uniform(0, 0.3)
                    await asyncio.sleep(sleep_s)

                impersonate_target = ["chrome131", "firefox", "safari"][attempt % 3]

                # Build headers dynamically
                headers = {
                    "Accept": "application/json, text/html, */*;q=0.9",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Referer": "https://www.reddit.com/",
                }

                cookie = os.environ.get("REDDIT_SESSION_COOKIE") or os.environ.get("REDDIT_SESSION")
                if cookie:
                    headers["Cookie"] = f"reddit_session={cookie}"
                else:
                    headers["Cookie"] = "csv=1; over18=1"

                if impersonate_target == "chrome131":
                    headers.update({
                        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                        "Sec-Ch-Ua-Mobile": "?0",
                        "Sec-Ch-Ua-Platform": '"Windows"',
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-origin",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    })

                try:
                    async with cffi_requests.AsyncSession(impersonate=impersonate_target, proxies=proxies_config, verify=False) as session:
                        resp = await session.request(
                            method=method,
                            url=url,
                            headers=headers,
                            allow_redirects=allow_redirects,
                            timeout=PROXY_TIMEOUT,
                        )

                    body_lower = resp.text[:2000].lower() if resp.text else ""
                    is_blocked = (
                        resp.status_code in (403, 429, 503, 520, 521, 522, 523, 524)
                        or "challenge platform" in body_lower
                        or "just a moment" in body_lower
                        or "access denied" in body_lower
                        or "enable javascript" in body_lower
                    )

                    if is_blocked:
                        if proxy:
                            PROXY_FAILURES[proxy] = PROXY_FAILURES.get(proxy, 0) + 1
                        last_err = f"Proxy blocked (HTTP {resp.status_code}) under {impersonate_target}"
                        print(f"[PROXY] attempt {attempt+1}/{MAX_RETRIES} blocked via {proxy}: HTTP {resp.status_code}")
                        all_timed_out = False
                        continue

                    # Success
                    if proxy:
                        PROXY_FAILURES[proxy] = max(0, PROXY_FAILURES.get(proxy, 0) - 1)
                    _record_proxy_success()
                    return resp

                except Exception as e:
                    err_str = str(e)
                    is_timeout = "timeout" in err_str.lower() or "timed out" in err_str.lower() or "28" in err_str
                    if not is_timeout:
                        all_timed_out = False
                    if proxy:
                        PROXY_FAILURES[proxy] = PROXY_FAILURES.get(proxy, 0) + 1
                    last_err = f"Proxy error under {impersonate_target}: {type(e).__name__}: {e}"
                    print(f"[PROXY] attempt {attempt+1}/{MAX_RETRIES} error via {proxy}: {last_err}")

            if all_timed_out:
                _record_proxy_all_timeout()
        else:
            if _is_circuit_open():
                print(f"[CIRCUIT] Proxy circuit open — skipping proxy, going direct immediately")

        # Phase 2 — Last-resort direct attempt (no proxy)
        for impersonate_target in ["chrome131", "firefox", "safari"]:
            try:
                print(f"[PROXY] Trying direct fetch with {impersonate_target}.")
                headers = {
                    "Accept": "application/json, text/html, */*;q=0.9",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Referer": "https://www.reddit.com/",
                }
                cookie = os.environ.get("REDDIT_SESSION_COOKIE") or os.environ.get("REDDIT_SESSION")
                if cookie:
                    headers["Cookie"] = f"reddit_session={cookie}"
                else:
                    headers["Cookie"] = "csv=1; over18=1"

                if impersonate_target == "chrome131":
                    headers.update({
                        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                        "Sec-Ch-Ua-Mobile": "?0",
                        "Sec-Ch-Ua-Platform": '"Windows"',
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-origin",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    })

                async with cffi_requests.AsyncSession(impersonate=impersonate_target) as session:
                    resp = await session.request(
                        method=method,
                        url=url,
                        headers=headers,
                        allow_redirects=allow_redirects,
                        timeout=12.0,
                    )
                body_lower = resp.text[:2000].lower() if resp.text else ""
                if resp.status_code not in (403, 429, 503, 520, 521, 522) and "just a moment" not in body_lower:
                    return resp
                last_err = f"Direct also blocked (HTTP {resp.status_code})"
            except Exception as e:
                last_err = f"Direct error: {type(e).__name__}: {e}"

        raise Exception(f"All fetch attempts failed. Last error: {last_err}")


async def bulk_stealth_fetch(url: str, method: str = "GET", allow_redirects: bool = True) -> StealthResponse:
    """
    Slow & Steady bulk fetch with residential proxy, strict rate limit backing-off,
    bandwidth optimization (HEAD / streaming first 8KB), and no direct fallback.
    """
    async with _BULK_SEMAPHORE:
        if _is_circuit_open():
            raise Exception("Proxy circuit breaker is open: proxy unavailable")

        proxy = get_healthy_proxy()
        proxies_config = {"http": proxy, "https": proxy} if proxy else None

        async def do_fetch() -> StealthResponse:
            targets = ["chrome131", "firefox", "safari"]
            last_fetch_err = None

            for impersonate_target in targets:
                # Build headers dynamically
                req_headers = {
                    "Accept": "application/json, text/html, */*;q=0.9",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Referer": "https://www.reddit.com/",
                    "Cookie": "csv=1; over18=1",
                }

                if impersonate_target == "chrome131":
                    req_headers.update({
                        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                        "Sec-Ch-Ua-Mobile": "?0",
                        "Sec-Ch-Ua-Platform": '"Windows"',
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-origin",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    })

                try:
                    async with cffi_requests.AsyncSession(impersonate=impersonate_target, proxies=proxies_config, verify=False) as session:
                        resp = await session.request(
                            method=method,
                            url=url,
                            headers=req_headers,
                            allow_redirects=allow_redirects,
                            timeout=20.0,
                            stream=(method != "HEAD")
                        )
                        
                        # Handle 429 inside the impersonation loop (retry once)
                        if resp.status_code == 429:
                            await resp.aclose()
                            print(f"[BULK] 429 rate-limit on proxy fetch with {impersonate_target}, waiting 60s before retry...")
                            await asyncio.sleep(60.0)
                            async with cffi_requests.AsyncSession(impersonate=impersonate_target, proxies=proxies_config, verify=False) as retry_session:
                                resp = await retry_session.request(
                                    method=method,
                                    url=url,
                                    headers=req_headers,
                                    allow_redirects=allow_redirects,
                                    timeout=20.0,
                                    stream=(method != "HEAD")
                                )
                        
                        if resp.status_code == 429:
                            await resp.aclose()
                            raise Exception("Proxy rate-limited after retry (HTTP 429)")

                        text_content = ""
                        body_lower = ""
                        if method != "HEAD":
                            raw_body = await read_stream_limit(resp, limit_bytes=8192)
                            text_content = raw_body.decode("utf-8", errors="ignore")
                            body_lower = text_content[:2000].lower()
                        else:
                            await resp.aclose()

                        is_blocked = (
                            resp.status_code in (403, 503, 520, 521, 522, 523, 524)
                            or "challenge platform" in body_lower
                            or "just a moment" in body_lower
                        )
                        if is_blocked:
                            raise Exception(f"Blocked (HTTP {resp.status_code})")

                        return StealthResponse(resp.status_code, text_content, dict(resp.headers), str(resp.url))

                except Exception as e:
                    last_fetch_err = e
                    print(f"[BULK DEBUG] Fetch with {impersonate_target} failed: {e}. Trying next browser...")
                    continue

            raise Exception(f"All impersonations failed. Last error: {last_fetch_err}")

        try:
            res = await do_fetch()
            if proxy and not IS_SINGLE_ROTATING_GATEWAY:
                PROXY_FAILURES[proxy] = max(0, PROXY_FAILURES.get(proxy, 0) - 1)
            _record_proxy_success()
            return res
        except Exception as e:
            err_str = str(e)
            is_timeout = "timeout" in err_str.lower() or "timed out" in err_str.lower() or "28" in err_str
            if is_timeout:
                _record_proxy_all_timeout()
            if proxy and not IS_SINGLE_ROTATING_GATEWAY:
                PROXY_FAILURES[proxy] = PROXY_FAILURES.get(proxy, 0) + 1
            print(f"[PROXY DEBUG] Bulk proxy fetch failed: {err_str}")
            raise Exception(f"Bulk fetch failed. Last error: {err_str}")




# ---------------------------------------------------------------------------
# Helpers used by the bulk endpoint
# ---------------------------------------------------------------------------

def _detect_url_type(url: str) -> str:
    """Return 'comment' or 'post' based on URL shape."""
    clean = url.split("?")[0].rstrip("/")
    if "/comment/" in clean:
        return "comment"
    if "/comments/" not in clean:
        return "post"
    after = clean.split("/comments/")[1]
    segs = [s for s in after.split("/") if s]
    # segs: [postId, title?, commentId?]
    if len(segs) >= 3 and re.match(r'^[a-z0-9]{4,}$', segs[2], re.I):
        return "comment"
    return "post"


async def _bulk_check_single(url: str) -> dict:
    """Check one URL (comment or post) and return a flat result dict with bandwidth & cache optimization."""
    start = time.time()
    try:
        # Resolve share links using HEAD only and caching
        resolved = get_from_cache(SHORTLINK_CACHE, url)
        if not resolved:
            resolved = url
            if "redd.it" in url or "/s/" in url:
                try:
                    r = await bulk_stealth_fetch(url, method="HEAD", allow_redirects=True)
                    if r.url and str(r.url) != url:
                        resolved = str(r.url)
                        set_in_cache(SHORTLINK_CACHE, url, resolved)
                except Exception:
                    pass

        # Strip query string + trailing slash
        clean = resolved.split("?")[0].rstrip("/")
        url_type = _detect_url_type(clean)

        # Check in-memory cache
        cached_res = get_from_cache(COMMENT_CACHE if url_type == "comment" else POST_CACHE, clean)
        if cached_res:
            elapsed = int((time.time() - start) * 1000)
            print(f"[BULK:CACHE_HIT] {clean} ({elapsed}ms)")
            res_copy = dict(cached_res)
            res_copy["url"] = url
            return res_copy

        if url_type == "comment":
            match = re.search(r'/comments/([^/]+)/[^/]+/([^/]+)', clean)
            if not match:
                return {"url": url, "type": "comment", "error": "Cannot parse comment URL", "data": None}
            post_id, comment_id = match.groups()
            sub_match = re.search(r'/r/([^/]+)', clean)
            subreddit = sub_match.group(1) if sub_match else "all"

            # 1. HEAD request check
            head_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/_/{comment_id}/"
            try:
                h_resp = await bulk_stealth_fetch(head_url, method="HEAD")
                if h_resp.status_code == 404:
                    res = {"url": url, "type": "comment", "error": None, "data": {
                        "status": "not_found", "author": None, "subreddit": subreddit,
                        "body_preview": None, "score": 0, "created_utc": None, "post_status": "deleted"
                    }}
                    set_in_cache(COMMENT_CACHE, clean, res)
                    return res
            except Exception as e:
                return {"url": url, "type": "comment", "error": f"HEAD failed: {str(e)}", "data": None}

            # 2. Narrow GET request with limit=0&context=0
            fetch_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/_/{comment_id}.json?raw_json=1&context=0&limit=0"
            try:
                resp = await bulk_stealth_fetch(fetch_url)
            except Exception as e:
                return {"url": url, "type": "comment", "error": str(e), "data": None}

            if resp.status_code == 404:
                res = {"url": url, "type": "comment", "error": None, "data": {
                    "status": "not_found", "author": None, "subreddit": subreddit,
                    "body_preview": None, "score": 0, "created_utc": None, "post_status": "deleted"
                }}
                set_in_cache(COMMENT_CACHE, clean, res)
                return res

            try:
                data = resp.json()
            except Exception:
                return {"url": url, "type": "comment", "error": "Invalid JSON from Reddit", "data": None}

            post_data = data[0]["data"]["children"][0]["data"]
            post_status = "deleted" if post_data.get("author") == "[deleted]" else (
                "removed" if post_data.get("removed_by_category") else "active"
            )
            comment_data = walk_comment_tree(data[1]["data"]["children"], comment_id)

            if not comment_data:
                res = {"url": url, "type": "comment", "error": None, "data": {
                    "status": "not_found", "author": None, "subreddit": subreddit,
                    "body_preview": None, "score": 0, "created_utc": None, "post_status": post_status
                }}
                set_in_cache(COMMENT_CACHE, clean, res)
                return res

            body = comment_data.get("body", "")
            author = comment_data.get("author")
            status = "live"
            if body == "[removed]":
                status = "removed"
            elif body == "[deleted]" and author == "[deleted]":
                status = "deleted"

            elapsed = int((time.time() - start) * 1000)
            print(f"[BULK:COMMENT] {comment_id} -> {status} ({elapsed}ms)")
            res = {"url": url, "type": "comment", "error": None, "data": {
                "status": status, "liveness": status,
                "author": author, "subreddit": comment_data.get("subreddit") or subreddit,
                "body_preview": body[:100] if body else None,
                "score": comment_data.get("score", 0),
                "upvotes": comment_data.get("score", 0),
                "depth": comment_data.get("depth", 0),
                "created_utc": comment_data.get("created_utc"),
                "createdAt": comment_data.get("created_utc"),
                "post_status": post_status, "error": None
            }}
            set_in_cache(COMMENT_CACHE, clean, res)
            return res

        else:  # post
            match = re.search(r'/comments/([^/]+)', clean)
            if not match:
                return {"url": url, "type": "post", "error": "Cannot parse post URL", "data": None}
            post_id = match.group(1)
            sub_match = re.search(r'/r/([^/]+)', clean)
            subreddit = sub_match.group(1) if sub_match else "all"

            # 1. HEAD request check
            head_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/"
            try:
                h_resp = await bulk_stealth_fetch(head_url, method="HEAD")
                if h_resp.status_code == 404:
                    res = {"url": url, "type": "post", "error": None, "data": {
                        "status": "not_found", "author": None, "subreddit": subreddit,
                        "title": None, "score": 0, "created_utc": None
                    }}
                    set_in_cache(POST_CACHE, clean, res)
                    return res
            except Exception as e:
                return {"url": url, "type": "post", "error": f"HEAD failed: {str(e)}", "data": None}

            # 2. Narrow GET request with limit=0
            fetch_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/.json?limit=0&raw_json=1"
            try:
                resp = await bulk_stealth_fetch(fetch_url)
            except Exception as e:
                return {"url": url, "type": "post", "error": str(e), "data": None}

            if resp.status_code == 404:
                res = {"url": url, "type": "post", "error": None, "data": {
                    "status": "not_found", "author": None, "subreddit": subreddit,
                    "title": None, "score": 0, "created_utc": None
                }}
                set_in_cache(POST_CACHE, clean, res)
                return res

            try:
                data = resp.json()
                post_data = data[0]["data"]["children"][0]["data"]
            except Exception:
                return {"url": url, "type": "post", "error": "Invalid JSON from Reddit", "data": None}

            removed_by = post_data.get("removed_by_category")
            author = post_data.get("author")
            selftext = post_data.get("selftext", "")
            status = "active"
            if removed_by == "spam":
                status = "spam"
            elif removed_by:
                status = "removed"
            elif selftext == "[removed]":
                status = "removed"
            elif author == "[deleted]":
                status = "deleted"

            elapsed = int((time.time() - start) * 1000)
            print(f"[BULK:POST] {post_id} -> {status} ({elapsed}ms)")
            res = {"url": url, "type": "post", "error": None, "data": {
                "status": status, "liveness": "live" if status == "active" else status,
                "author": author, "subreddit": post_data.get("subreddit") or subreddit,
                "title": post_data.get("title"),
                "score": post_data.get("score", 0),
                "upvotes": post_data.get("score", 0),
                "num_comments": post_data.get("num_comments", 0),
                "removed_by_category": removed_by,
                "created_utc": post_data.get("created_utc"),
                "createdAt": post_data.get("created_utc"),
                "error": None
            }}
            set_in_cache(POST_CACHE, clean, res)
            return res

    except Exception as e:
        return {"url": url, "type": "unknown", "error": str(e), "data": None}


async def _bulk_check_account(username: str) -> dict:
    """Check one Reddit account. Returns dict with account data."""
    try:
        fetch_url = f"https://old.reddit.com/user/{username}/about.json?raw_json=1"
        resp = await bulk_stealth_fetch(fetch_url)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return {
                "username": data.get("name", username),
                "status": "suspended" if data.get("is_suspended") else "active",
                "total_karma": data.get("total_karma", 0),
                "created_utc": data.get("created_utc"),
                "avatar_url": data.get("icon_img"),
                "last_active_utc": None,
                "error": None
            }
        elif resp.status_code == 404:
            # Could be deleted or shadowbanned — check HTML
            try:
                html_resp = await bulk_stealth_fetch(f"https://old.reddit.com/user/{username}/")
                status = "shadowbanned" if html_resp.status_code == 200 else "deleted"
            except Exception:
                status = "deleted"
            return {"username": username, "status": status, "total_karma": 0,
                    "created_utc": None, "avatar_url": None, "last_active_utc": None, "error": None}
        else:
            return {"username": username, "status": "error", "total_karma": 0,
                    "created_utc": None, "avatar_url": None, "last_active_utc": None,
                    "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"username": username, "status": "error", "total_karma": 0,
                "created_utc": None, "avatar_url": None, "last_active_utc": None, "error": str(e)}


@app.post("/api/external/bulk/check")
async def bulk_check(request: Request):
    """
    Batch endpoint for the Reddit Inspector.

    Accepts: {"urls": ["...", ...], "include_author": true}
    Returns: {"results": [{url, type, data, author, error}, ...]}

    Throttles checks: chunking (20 URLs/chunk), 10s delay between chunks,
    caches resolved links and statuses, and rate-limits author fetches.
    """
    body = await request.json()
    raw_urls: list = body.get("urls", [])
    include_author: bool = body.get("include_author", True)
    urls = [u.strip() for u in raw_urls if isinstance(u, str) and u.strip()][:245]  # Max 245 URLs

    if not urls:
        return Response(content=json.dumps({"error": "No URLs provided"}), status_code=400, media_type="application/json")

    # Run checks sequentially in chunks of 20
    chunk_size = 20
    results = []
    for i in range(0, len(urls), chunk_size):
        chunk = urls[i:i+chunk_size]
        url_tasks = [_bulk_check_single(u) for u in chunk]
        chunk_results = await asyncio.gather(*url_tasks, return_exceptions=False)
        results.extend(chunk_results)
        
        if i + chunk_size < len(urls):
            print(f"[BULK] Chunk {i//chunk_size + 1} processed. Cool down for 10s...")
            await asyncio.sleep(10.0)

    # Throttled & Cached Author Fetching
    if include_author:
        from collections import Counter
        author_counts = Counter(
            r["data"]["author"]
            for r in results
            if r.get("data") and r["data"].get("author")
            and r["data"]["author"] not in ("[deleted]", None)
        )

        author_map = {}
        authors_to_query = []

        for author, count in author_counts.items():
            cached_author = get_from_cache(AUTHOR_CACHE, author)
            if cached_author:
                author_map[author] = cached_author
            elif count > 3:
                authors_to_query.append(author)
            else:
                # Skip fetch for low-frequency author, use placeholder
                placeholder = {
                    "username": author,
                    "status": "active",
                    "total_karma": 0,
                    "created_utc": None,
                    "avatar_url": None,
                    "last_active_utc": None,
                    "error": "Skipped fetch (low frequency)"
                }
                author_map[author] = placeholder

        # Fetch remaining authors in batches of 5 with 2s delay and a concurrency of 2
        async def check_author_with_semaphore(a: str) -> dict:
            async with _AUTHOR_SEMAPHORE:
                res = await _bulk_check_account(a)
                if res.get("status") != "error":
                    set_in_cache(AUTHOR_CACHE, a, res)
                return res

        for j in range(0, len(authors_to_query), 5):
            batch = authors_to_query[j:j+5]
            tasks = [check_author_with_semaphore(a) for a in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=False)
            for d in batch_results:
                if d.get("username"):
                    author_map[d["username"]] = d
            if j + 5 < len(authors_to_query):
                await asyncio.sleep(2.0)

        for r in results:
            author = r.get("data", {}) and r["data"].get("author")
            if not author:
                r["author"] = None
            elif author == "[deleted]":
                r["author"] = {"username": "[deleted]", "status": "deleted",
                               "total_karma": 0, "created_utc": None, "avatar_url": None,
                               "last_active_utc": None, "error": None}
            else:
                r["author"] = author_map.get(author)
    else:
        for r in results:
            r["author"] = None

    return Response(content=json.dumps({"results": results}), status_code=200, media_type="application/json")

async def resolve_url(url: str) -> str:
    """Resolve shortlinks and normalize reddit URLs."""
    try:
        parsed = urlparse(url)
        
        # If it's a share link or redd.it link, resolve it
        if "redd.it" in url or "/s/" in url:
            resolved = False
            
            # Step 1: Try HEAD with allow_redirects=True
            try:
                resp = await stealth_fetch(url, method="HEAD", allow_redirects=True)
                if resp.url and str(resp.url) != url and "redd.it" not in str(resp.url) and "/s/" not in str(resp.url):
                    url = str(resp.url)
                    resolved = True
            except Exception:
                pass
                
            # Step 2: Try GET with allow_redirects=True
            if not resolved:
                try:
                    resp = await stealth_fetch(url, method="GET", allow_redirects=True)
                    if resp.url and str(resp.url) != url and "redd.it" not in str(resp.url) and "/s/" not in str(resp.url):
                        url = str(resp.url)
                        resolved = True
                    elif resp.status_code == 200:
                        # Parse body for canonical URL or JS redirects
                        import re
                        patterns = [
                            r'<link\s+rel=["\']canonical["\']\s+href=["\']([^"\']+)["\']',
                            r'sh-location=["\']([^"\']+)["\']',
                            r'window\.location\.replace\([\'"]([^\'"]+)[\'"]\)',
                            r'window\.location\s*=\s*[\'"]([^\'"]+)[\'"]'
                        ]
                        for pat in patterns:
                            match = re.search(pat, resp.text)
                            if match:
                                url = match.group(1).replace("&amp;", "&")
                                resolved = True
                                break
                except Exception:
                    pass
            
            if not resolved:
                raise ValueError("Share link could not be resolved")
                
            if "/s/" in url and "/comments/" not in url:
                raise ValueError("Share link resolved to invalid URL (no /comments/ path found)")
                
        # Normalize to old.reddit.com
        parsed = urlparse(url)
        if parsed.netloc in ["reddit.com", "www.reddit.com"]:
            url = url.replace(parsed.netloc, "old.reddit.com")
            
        # Strip query params like js_challenge, token, etc.
        parsed = urlparse(url)
        clean_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return clean_url
    except ValueError as e:
        raise e
    except Exception:
        return url


def walk_comment_tree(tree_list: List[Any], target_id: str) -> Optional[Dict]:
    for item in tree_list:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind == "more":
            continue
            
        data = item.get("data", {})
        if data.get("id") == target_id:
            return data
            
        replies = data.get("replies")
        if isinstance(replies, dict) and "data" in replies and "children" in replies["data"]:
            found = walk_comment_tree(replies["data"]["children"], target_id)
            if found:
                return found
    return None


@app.get("/api/external/check/comment")
async def check_comment(url: str):
    start_time = time.time()
    try:
        try:
            resolved_url = await resolve_url(url)
        except ValueError as ve:
            return Response(content=json.dumps({"status": "error", "error": str(ve)}), status_code=400, media_type="application/json")
        
        # Extract post_id and comment_id
        # Expected format: .../comments/{post_id}/title/{comment_id} or .../comments/{post_id}/_/{comment_id}
        match = re.search(r'/comments/([^/]+)/[^/]+/([^/]+)', resolved_url)
        if not match:
            return Response(
                content=json.dumps({"status": "error", "error": "Invalid comment URL format"}), 
                status_code=400, media_type="application/json"
            )
            
        post_id, comment_id = match.groups()
        
        # Subreddit extraction
        sub_match = re.search(r'/r/([^/]+)', resolved_url)
        subreddit = sub_match.group(1) if sub_match else "all"
        
        # Initial Fetch (Narrow)
        fetch_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/_/{comment_id}.json?raw_json=1&context=0&limit=1"
        try:
            resp = await stealth_fetch(fetch_url)
        except Exception as e:
            return Response(
                content=json.dumps({"status": "error", "error": str(e)}), 
                status_code=403, media_type="application/json"
            )
            
        if resp.status_code == 404:
            return Response(
                content=json.dumps({"status": "not_found", "error": "Thread not found", "post_status": "deleted"}), 
                status_code=404, media_type="application/json"
            )
            
        try:
            data = resp.json()
        except:
            return Response(content=json.dumps({"status": "error", "error": "Invalid JSON"}), status_code=403, media_type="application/json")
            
        post_data = data[0]["data"]["children"][0]["data"]
        post_status = "active"
        if post_data.get("author") == "[deleted]":
            post_status = "deleted"
        elif post_data.get("removed_by_category"):
            post_status = "removed"
            
        comment_tree = data[1]["data"]["children"]
        comment_data = walk_comment_tree(comment_tree, comment_id)
        
        if comment_data:
            FALLBACK_STATS["narrow_hit"] += 1
        else:
            FALLBACK_STATS["fallback_fired"] += 1
            print(f"[FALLBACK_FIRED] {comment_id} — narrow fetch (limit=1,context=0) missed target, retrying wide")
            
            # Fallback (Wide)
            fetch_url_ctx = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/_/{comment_id}.json?raw_json=1"
            try:
                resp2 = await stealth_fetch(fetch_url_ctx)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    comment_data = walk_comment_tree(data2[1]["data"]["children"], comment_id)
                    if comment_data:
                        FALLBACK_STATS["fallback_hit"] += 1
                    else:
                        FALLBACK_STATS["total_miss"] += 1
            except Exception:
                FALLBACK_STATS["total_miss"] += 1
                
        if not comment_data:
            return Response(
                content=json.dumps({"status": "not_found", "error": "Comment not found in tree", "post_status": post_status}), 
                status_code=404, media_type="application/json"
            )
            
        body = comment_data.get("body", "")
        author = comment_data.get("author")
        
        status = "live"
        if body == "[removed]":
            status = "removed"
        elif body == "[deleted]" and author == "[deleted]":
            status = "deleted"
            
        result = {
            "status": status,
            "author": author,
            "subreddit": comment_data.get("subreddit") or subreddit,
            "body_preview": body[:100] if body else None,
            "score": comment_data.get("score", 0),
            "upvotes": comment_data.get("score", 0),
            "depth": comment_data.get("depth", 0),
            "created_utc": comment_data.get("created_utc"),
            "createdAt": comment_data.get("created_utc"),
            "post_status": post_status,
            "error": None
        }
        
        # Backward compatibility aliases for the Node.js bot
        result["liveness"] = status if status != "live" else "live"
        
        http_status = 200 if status == "live" else 404
        
        print(f"[COMMENT] {comment_id} -> {status} ({int((time.time() - start_time)*1000)}ms)")
        
        # Wrap in success/data to maintain backward compatibility with older Node.js parser
        final_response = {"success": True, "data": result}
        return Response(content=json.dumps(final_response), status_code=http_status, media_type="application/json")

    except Exception as e:
        return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=500, media_type="application/json")


@app.get("/api/external/check/post")
async def check_post(url: str):
    start_time = time.time()
    try:
        try:
            resolved_url = await resolve_url(url)
        except ValueError as ve:
            return Response(content=json.dumps({"status": "error", "error": str(ve)}), status_code=400, media_type="application/json")
        
        match = re.search(r'/comments/([^/]+)', resolved_url)
        if not match:
            return Response(content=json.dumps({"status": "error", "error": "Invalid post URL"}), status_code=400, media_type="application/json")
        post_id = match.group(1)
        
        sub_match = re.search(r'/r/([^/]+)', resolved_url)
        subreddit = sub_match.group(1) if sub_match else "all"
        
        fetch_url = f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/.json?limit=1&raw_json=1"
        try:
            resp = await stealth_fetch(fetch_url)
        except Exception as e:
            return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=403, media_type="application/json")
            
        if resp.status_code == 404:
            return Response(content=json.dumps({"status": "not_found", "error": "Post not found"}), status_code=404, media_type="application/json")
            
        data = resp.json()
        post_data = data[0]["data"]["children"][0]["data"]
        
        removed_by = post_data.get("removed_by_category")
        author = post_data.get("author")
        selftext = post_data.get("selftext", "")
        
        status = "active"
        if removed_by == "spam":
            status = "spam"
        elif removed_by:
            status = "removed"
        elif selftext == "[removed]":
            status = "removed"
        elif author == "[deleted]":
            status = "deleted"
            
        result = {
            "status": status,
            "author": author,
            "subreddit": post_data.get("subreddit") or subreddit,
            "title": post_data.get("title"),
            "score": post_data.get("score", 0),
            "upvotes": post_data.get("score", 0),
            "num_comments": post_data.get("num_comments", 0),
            "removed_by_category": removed_by,
            "created_utc": post_data.get("created_utc"),
            "createdAt": post_data.get("created_utc"),
            "error": None
        }
        
        # Backward compatibility aliases for the Node.js bot
        if status in ("active"):
            result["liveness"] = "live"
        elif status in ("spam", "removed"):
            result["liveness"] = "removed"
        else:
            result["liveness"] = status
        
        http_status = 200 if status == "active" else 404
        print(f"[POST] {post_id} -> {status} ({int((time.time() - start_time)*1000)}ms)")
        
        # Wrap in success/data to maintain backward compatibility
        final_response = {"success": True, "data": result}
        return Response(content=json.dumps(final_response), status_code=http_status, media_type="application/json")

    except Exception as e:
        return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=500, media_type="application/json")


@app.get("/api/external/check/account")
async def check_account(username: str, include_activity: bool = False):
    start_time = time.time()
    try:
        username = username.lstrip("u/").split("/")[-1]
        
        fetch_url = f"https://old.reddit.com/user/{username}/about.json?raw_json=1"
        try:
            resp = await stealth_fetch(fetch_url)
        except Exception as e:
            return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=403, media_type="application/json")
            
        if resp.status_code == 200:
            data = resp.json()
            user_data = data.get("data", {})
            is_suspended = user_data.get("is_suspended", False)
            status = "suspended" if is_suspended else "active"
            
            last_active_utc = None
            if include_activity and status == "active":
                try:
                    overview_url = f"https://old.reddit.com/user/{username}/.json?limit=1&raw_json=1"
                    overview_resp = await stealth_fetch(overview_url)
                    if overview_resp.status_code == 200:
                        overview_data = overview_resp.json()
                        children = overview_data.get("data", {}).get("children", [])
                        if children:
                            last_active_utc = children[0].get("data", {}).get("created_utc")
                except Exception:
                    pass
            
            result = {
                "status": status,
                "username": user_data.get("name", username),
                "total_karma": user_data.get("total_karma", 0),
                "created_utc": user_data.get("created_utc"),
                "avatar_url": user_data.get("icon_img"),
                "last_active_utc": last_active_utc,
                "error": None
            }
            print(f"[ACCOUNT] {username} -> {status} ({int((time.time() - start_time)*1000)}ms)")
            return Response(content=json.dumps(result), status_code=200, media_type="application/json")
            
        elif resp.status_code == 404:
            # Check shadowbanned vs deleted
            html_url = f"https://old.reddit.com/user/{username}/"
            try:
                html_resp = await stealth_fetch(html_url)
                if html_resp.status_code == 200:
                    status = "shadowbanned"
                else:
                    status = "deleted"
            except Exception:
                status = "deleted"
                
            result = {
                "status": status,
                "username": username,
                "total_karma": 0,
                "created_utc": None,
                "avatar_url": None,
                "last_active_utc": None,
                "error": None
            }
            
            # Backwards compat for node bot calling new endpoint? Node bot doesn't call this yet.
            # But we will return 404 for deleted/shadowbanned so standard semantics apply
            print(f"[ACCOUNT] {username} -> {status} ({int((time.time() - start_time)*1000)}ms)")
            return Response(content=json.dumps(result), status_code=404, media_type="application/json")
            
        else:
            return Response(content=json.dumps({"status": "error", "error": f"HTTP {resp.status_code}"}), status_code=403, media_type="application/json")

    except Exception as e:
        return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=500, media_type="application/json")



@app.get("/api/external/proxy/json")
async def proxy_json(url: str):
    start_time = time.time()
    try:
        resp = await stealth_fetch(url)
        if resp.status_code == 200:
            print(f"[PROXY_JSON] 200 OK ({int((time.time() - start_time)*1000)}ms)")
            return Response(content=resp.text, status_code=200, media_type="application/json")
        else:
            print(f"[PROXY_JSON] {resp.status_code} ({int((time.time() - start_time)*1000)}ms)")
            return Response(content=resp.text, status_code=resp.status_code, media_type="application/json")
    except Exception as e:
        print(f"[PROXY_JSON] ERROR: {str(e)}")
        return Response(content=json.dumps({"status": "error", "error": str(e)}), status_code=403, media_type="application/json")


@app.get("/api/external/stats/fallback")
async def fallback_stats():
    total = FALLBACK_STATS["narrow_hit"] + FALLBACK_STATS["fallback_fired"]
    rate = (FALLBACK_STATS["fallback_fired"] / total * 100) if total else 0
    return Response(
        content=json.dumps({**FALLBACK_STATS, "total_requests": total, "fallback_rate_pct": round(rate, 2)}),
        status_code=200, media_type="application/json"
    )


@app.get("/api/external/admin/reload-proxies")
async def reload_proxies_endpoint():
    """Hot-reload proxies.txt without restarting the Space."""
    old_count = len(PROXIES)
    load_proxies()
    PROXY_FAILURES.clear()
    return Response(
        content=json.dumps({
            "status": "ok",
            "old_proxy_count": old_count,
            "new_proxy_count": len(PROXIES),
            "message": "Proxies reloaded and failure counters reset."
        }),
        status_code=200, media_type="application/json"
    )


@app.get("/health")
async def health():
    """Quick health check — confirms the API is up and reports proxy pool size."""
    healthy = [p for p in PROXIES if PROXY_FAILURES.get(p, 0) < MAX_PROXY_FAILURES]
    circuit_open = _is_circuit_open()
    circuit_resets_in = max(0, int(_PROXY_CIRCUIT_OPEN_UNTIL - time.time())) if circuit_open else 0
    return Response(
        content=json.dumps({
            "status": "ok",
            "proxy_enabled": _PROXY_ENABLED,
            "session_cookie_set": bool(os.environ.get("REDDIT_SESSION_COOKIE") or os.environ.get("REDDIT_SESSION")),
            "proxy_list_url_set": bool(os.environ.get("PROXY_LIST_URL", "").strip()),
            "proxy_total": len(PROXIES),
            "proxy_healthy": len(healthy),
            "proxy_failures": {k: v for k, v in PROXY_FAILURES.items() if v > 0},
            "proxy_circuit_open": circuit_open,
            "proxy_circuit_resets_in_secs": circuit_resets_in,
            "proxy_consecutive_timeouts": _PROXY_CONSECUTIVE_TIMEOUTS,
            "fallback_stats": FALLBACK_STATS,
        }),
        status_code=200, media_type="application/json"
    )


@app.post("/reload-proxies")
async def reload_proxies_endpoint():
    """Reload proxy list from proxies.txt + PROXY_LIST_URL without restarting."""
    old_count = len(PROXIES)
    PROXY_FAILURES.clear()
    load_proxies()
    return Response(
        content=json.dumps({
            "status": "reloaded",
            "proxies_before": old_count,
            "proxies_after": len(PROXIES),
        }),
        status_code=200, media_type="application/json"
    )
