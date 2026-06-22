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
MAX_PROXY_FAILURES = 3
FALLBACK_STATS = {"narrow_hit": 0, "fallback_fired": 0, "fallback_hit": 0, "total_miss": 0}

def load_proxies():
    global PROXIES
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        
        formatted = []
        for line in lines:
            line = line.replace("http://", "").replace("https://", "").replace("socks5://", "")
            if "@" in line:
                formatted.append(f"http://{line}")
                continue
                
            parts = line.split(":")
            if len(parts) >= 4:
                host, port, user, pw = parts[:4]
                formatted.append(f"http://{user}:{pw}@{host}:{port}")
            else:
                formatted.append(f"http://{line}")
                
        PROXIES = formatted
        print(f"Loaded {len(PROXIES)} proxies.")

load_proxies()

def get_healthy_proxy() -> Optional[str]:
    if not PROXIES:
        return None
    healthy = [p for p in PROXIES if PROXY_FAILURES.get(p, 0) < MAX_PROXY_FAILURES]
    if not healthy:
        # If all fail, reset failures and try again
        PROXY_FAILURES.clear()
        healthy = PROXIES
    return random.choice(healthy)

async def stealth_fetch(url: str, method: str = "GET", allow_redirects: bool = True) -> cffi_requests.Response:
    max_retries = 3
    last_err = None
    
    headers = {
        "Accept": "application/json, text/html",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, br",
    }
    
    cookie = os.environ.get("REDDIT_SESSION_COOKIE")
    if cookie:
        headers["Cookie"] = f"reddit_session={cookie}"

    # Attempt 1: Direct request without proxy
    try:
        async with cffi_requests.AsyncSession(impersonate="chrome120") as session:
            resp = await session.request(
                method=method,
                url=url,
                headers=headers,
                allow_redirects=allow_redirects,
                timeout=15.0
            )
            
            # If not blocked, return immediately
            if resp.status_code not in (403, 429) and "challenge platform" not in resp.text.lower() and "just a moment" not in resp.text.lower():
                return resp
                
            last_err = f"Direct request blocked (HTTP {resp.status_code})"
    except Exception as e:
        last_err = f"Direct request error: {str(e)}"

    # Attempt 2+: Use proxies
    for attempt in range(max_retries):
        proxy = get_healthy_proxy()
        proxies_config = {"http": proxy, "https": proxy} if proxy else None
        
        try:
            # We use AsyncSession for async curl_cffi requests
            async with cffi_requests.AsyncSession(impersonate="chrome120", proxies=proxies_config) as session:
                resp = await session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    allow_redirects=allow_redirects,
                    timeout=15.0
                )
                
                # Check for Cloudflare / block signals
                if resp.status_code in (403, 429) or "challenge platform" in resp.text.lower() or "just a moment" in resp.text.lower():
                    if proxy:
                        PROXY_FAILURES[proxy] = PROXY_FAILURES.get(proxy, 0) + 1
                    last_err = f"Proxy blocked (HTTP {resp.status_code})"
                    await asyncio.sleep(random.uniform(1.0, 2.5))
                    continue
                    
                # Success or standard error like 404
                if proxy:
                    PROXY_FAILURES[proxy] = max(0, PROXY_FAILURES.get(proxy, 0) - 1)
                return resp
                
        except Exception as e:
            if proxy:
                PROXY_FAILURES[proxy] = PROXY_FAILURES.get(proxy, 0) + 1
            last_err = f"Proxy error: {str(e)}"
            
    raise Exception(f"Direct + {max_retries} proxy attempts failed. Last error: {last_err}")


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
