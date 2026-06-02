"""
redditOS Multi-Portal Dynamic Platform — Role-Based Access Control Web Application.
Serves separate Earner (/login, /earner), Client (/client/login, /client),
and Admin (/admin/login, /admin) interfaces with secure PBKDF2 credentials,
custom HTTPOnly signed session cookies, client-side OCR validation, and bulk auditors.
Zero emojis used for a professional, clean corporate UI aesthetic.
"""

from __future__ import annotations

import asyncio
import logging
import json
import os
import hashlib
import base64
import secrets
import hmac
import time
import uuid
from datetime import datetime, timezone, timedelta
from aiohttp import web
import discord
from discord.ext import commands

import bot.db as db
from bot.config import settings
from fetcher.fetch_router import get_router
from fetcher.json_fetcher import _extract_post_parts, _extract_comment_id

logger = logging.getLogger(__name__)

# Create proofs dir
os.makedirs("data/proofs", exist_ok=True)

# ─── SECURE CREDENTIALS & SESSIONS HASHING ────────────────────────────────────

def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with 100,000 iterations and random 16-byte salt."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return base64.b64encode(salt + key).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """Verify password by rebuilding PBKDF2-HMAC-SHA256 key from stored base64 signature."""
    try:
        decoded = base64.b64decode(hashed.encode('utf-8'))
        salt = decoded[:16]
        stored_key = decoded[16:]
        new_key = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return secrets.compare_digest(stored_key, new_key)
    except Exception:
        return False

def sign_session(username: str, role: str) -> str:
    """Sign cookie values using standard hmac signature to prevent tampering."""
    timestamp = str(int(time.time()))
    payload = f"{username}|{role}|{timestamp}"
    sig = hmac.new(settings.discord_bot_token.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"

def verify_session(cookie_value: str) -> tuple[str, str] | None:
    """Verify cookie value and validate timestamp and signature authenticity."""
    try:
        parts = cookie_value.split("|")
        if len(parts) != 4:
            return None
        username, role, timestamp, sig = parts
        payload = f"{username}|{role}|{timestamp}"
        expected_sig = hmac.new(settings.discord_bot_token.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(sig, expected_sig):
            return None
        # Valid for 7 days
        if int(time.time()) - int(timestamp) > 7 * 86400:
            return None
        return username, role
    except Exception:
        return None

def role_required(allowed_roles: list[str], login_redirect: str = "/login"):
    """Decorator/Middleware generator to enforce role-based access validation on web handlers."""
    def decorator(handler):
        async def wrapper(request: web.Request):
            cookie = request.cookies.get("session_token")
            if not cookie:
                if request.path.startswith("/api/"):
                    return web.json_response({"success": False, "message": "Unauthorized session"}, status=401)
                return web.HTTPFound(login_redirect)
            
            session = verify_session(cookie)
            if not session:
                if request.path.startswith("/api/"):
                    return web.json_response({"success": False, "message": "Invalid session"}, status=401)
                return web.HTTPFound(login_redirect)
            
            username, role = session
            if role != "dev" and role not in allowed_roles:
                if request.path.startswith("/api/"):
                    return web.json_response({"success": False, "message": "Access Denied: Forbidden role"}, status=403)
                return web.Response(text="<h1>Access Denied: Forbidden</h1>", content_type="text/html", status=403)
            
            request["username"] = username
            request["role"] = role
            return await handler(request)
        return wrapper
    return decorator

def get_system_telemetry() -> dict:
    """Read actual CPU/Memory stats on Linux platform directly from /proc virtual filesystem."""
    try:
        with open("/proc/loadavg", "r") as f:
            load = float(f.read().split()[0])
            cpu_usage = min(100.0, load * 100.0)
    except Exception:
        cpu_usage = 8.4

    try:
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
            mem_total = 1024
            mem_free = 512
            for line in lines:
                if "MemTotal" in line:
                    mem_total = int(line.split()[1])
                elif "MemAvailable" in line:
                    mem_free = int(line.split()[1])
            mem_usage = round((1.0 - (mem_free / mem_total)) * 100.0, 1)
    except Exception:
        mem_usage = 36.8

    return {
        "cpu": cpu_usage,
        "memory": mem_usage
    }

# ─── FRONTEND: ISOLATED LOGIN PAGES (EMOJI-FREE) ──────────────────────────────

LOGIN_EARNER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Earner Access Gateway</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #060709;
            --glass-bg: rgba(18, 20, 29, 0.7);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-purple: #9d4edd;
            --color-purple-hover: #b576f7;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: radial-gradient(circle at 10% 20%, rgba(157, 78, 221, 0.18) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; overflow: hidden;
        }
        .login-card {
            background: var(--glass-bg);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--glass-border);
            border-radius: 24px;
            padding: 40px;
            width: 100%; max-width: 450px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
            position: relative;
            animation: cardSlideIn 0.5s cubic-bezier(0.4, 0, 0.2, 1) forwards;
        }
        .login-card::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
            background: linear-gradient(90deg, rgba(255,255,255,0.12) 0%, rgba(255,255,255,0) 100%);
        }
        .logo-section { text-align: center; margin-bottom: 30px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #e0aaff 0%, var(--color-purple) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .logo-sub { font-size: 13px; color: var(--text-secondary); margin-top: 4px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
        input { width: 100%; background: rgba(0, 0, 0, 0.4); border: 1px solid var(--glass-border); border-radius: 10px; padding: 14px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px; transition: all 0.2s ease; }
        input:focus { border-color: var(--color-purple); box-shadow: 0 0 12px rgba(157, 78, 221, 0.25); }
        .btn-submit { width: 100%; background: var(--color-purple); color: #fff; padding: 14px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 15px; font-weight: 700; border: none; cursor: pointer; transition: all 0.2s ease; box-shadow: 0 4px 15px rgba(157, 78, 221, 0.2); margin-top: 10px; }
        .btn-submit:hover { background: var(--color-purple-hover); box-shadow: 0 6px 20px rgba(157, 78, 221, 0.4); transform: translateY(-1px); }
        .toggle-link { text-align: center; font-size: 13px; color: var(--text-secondary); margin-top: 20px; }
        .toggle-link span { color: var(--color-purple-hover); cursor: pointer; font-weight: 600; text-decoration: underline; }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; }
        .toast { background: rgba(18, 20, 29, 0.9); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-purple); padding: 14px 20px; border-radius: 8px; font-size: 13px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
        .toast.error { border-left-color: #ef476f; }
        @keyframes cardSlideIn { from { opacity: 0; transform: translateY(15px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-section">
            <span class="logo-text">redditOS</span>
            <div class="logo-sub">Earner Portal Gateway</div>
        </div>

        <div id="auth-panel">
            <form onsubmit="submitLogin(event)">
                <div class="form-group">
                    <label for="in-username">Discord ID</label>
                    <input type="text" id="in-username" placeholder="e.g. 104523904928" required>
                </div>
                <div class="form-group">
                    <label for="in-password">Password</label>
                    <input type="password" id="in-password" placeholder="••••••••" required>
                </div>
                <button type="submit" class="btn-submit">Enter Earner Portal</button>
            </form>
        </div>

        <div class="toggle-link" id="lnk-register-wrap">
            First time Earner? <span onclick="showRegisterWizard()">Secure account here</span>
        </div>
    </div>
    <div class="toast-container" id="toast-wrap"></div>

    <script>
        let isRegistering = false;
        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function submitLogin(e) {
            e.preventDefault();
            const username = document.getElementById('in-username').value.trim();
            const password = document.getElementById('in-password').value.trim();
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ username, password, portal: 'earner' })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Welcome back! Launching console...');
                    setTimeout(() => { window.location.href = '/earner'; }, 1000);
                } else {
                    showToast(data.message || 'Incorrect credentials.', 'error');
                }
            } catch (err) {
                showToast('Authentication server down.', 'error');
            }
        }

        function showRegisterWizard() {
            isRegistering = true;
            document.getElementById('lnk-register-wrap').innerHTML = 'Already secured? <span onclick="cancelRegister()">Log in here</span>';
            document.getElementById('auth-panel').innerHTML = `
                <form onsubmit="submitRegister(event)">
                    <div style="font-size: 13px; color: var(--text-secondary); line-height: 1.5; margin-bottom: 16px; border-left: 2px solid var(--color-purple); padding-left: 10px;">
                        Run /weblogin in Discord to receive a temporary 6-digit access code.
                    </div>
                    <div class="form-group">
                        <label for="reg-discord">Discord ID</label>
                        <input type="text" id="reg-discord" placeholder="e.g. 104523904928" required>
                    </div>
                    <div class="form-group">
                        <label for="reg-token">6-Digit Access Token</label>
                        <input type="text" id="reg-token" placeholder="e.g. 483920" maxlength="6" required>
                    </div>
                    <div class="form-group">
                        <label for="reg-password">Choose Web Password</label>
                        <input type="password" id="reg-password" placeholder="••••••••" required>
                    </div>
                    <button type="submit" class="btn-submit">Secure Account & Login</button>
                </form>
            `;
        }

        function cancelRegister() {
            isRegistering = false;
            document.getElementById('lnk-register-wrap').innerHTML = 'First time Earner? <span onclick="showRegisterWizard()">Secure account here</span>';
            document.getElementById('auth-panel').innerHTML = `
                <form onsubmit="submitLogin(event)">
                    <div class="form-group">
                        <label for="in-username">Discord ID</label>
                        <input type="text" id="in-username" placeholder="e.g. 104523904928" required>
                    </div>
                    <div class="form-group">
                        <label for="in-password">Password</label>
                        <input type="password" id="in-password" placeholder="••••••••" required>
                    </div>
                    <button type="submit" class="btn-submit">Enter Earner Portal</button>
                </form>
            `;
        }

        async function submitRegister(e) {
            e.preventDefault();
            const discord_id = document.getElementById('reg-discord').value.trim();
            const token = document.getElementById('reg-token').value.trim();
            const password = document.getElementById('reg-password').value.trim();
            try {
                const res = await fetch('/api/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ discord_id, token, password })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Account secured! Log in now.');
                    cancelRegister();
                    document.getElementById('in-username').value = discord_id;
                } else {
                    showToast(data.message || 'Registration failed.', 'error');
                }
            } catch (err) {
                showToast('Network error during registration.', 'error');
            }
        }
    </script>
</body>
</html>
"""

LOGIN_CLIENT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Client Seeder Portal</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #050608;
            --glass-bg: rgba(16, 22, 20, 0.7);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-emerald: #06d6a0;
            --color-emerald-hover: #34e8ba;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: radial-gradient(circle at 90% 10%, rgba(6, 214, 160, 0.12) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; overflow: hidden;
        }
        .login-card {
            background: var(--glass-bg);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--glass-border);
            border-radius: 24px;
            padding: 40px;
            width: 100%; max-width: 440px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
            position: relative;
            animation: cardSlideIn 0.5s ease forwards;
        }
        .logo-section { text-align: center; margin-bottom: 30px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #a7f3d0 0%, var(--color-emerald) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .logo-sub { font-size: 13px; color: var(--text-secondary); margin-top: 4px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
        input { width: 100%; background: rgba(0, 0, 0, 0.45); border: 1px solid var(--glass-border); border-radius: 10px; padding: 14px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px; transition: all 0.2s ease; }
        input:focus { border-color: var(--color-emerald); box-shadow: 0 0 12px rgba(6, 214, 160, 0.2); }
        .btn-submit { width: 100%; background: var(--color-emerald); color: #000; padding: 14px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 15px; font-weight: 700; border: none; cursor: pointer; transition: all 0.2s ease; box-shadow: 0 4px 15px rgba(6, 214, 160, 0.15); margin-top: 10px; }
        .btn-submit:hover { background: var(--color-emerald-hover); box-shadow: 0 6px 20px rgba(6, 214, 160, 0.35); transform: translateY(-1px); }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; }
        .toast { background: rgba(16, 22, 20, 0.95); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-emerald); padding: 14px 20px; border-radius: 8px; font-size: 13px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
        .toast.error { border-left-color: #ef476f; }
        @keyframes cardSlideIn { from { opacity: 0; transform: scale(0.98); } to { opacity: 1; transform: scale(1); } }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-section">
            <span class="logo-text">redditOS</span>
            <div class="logo-sub">Client Campaign Portal</div>
        </div>

        <form onsubmit="submitLogin(event)">
            <div class="form-group">
                <label for="in-username">Advertiser Username</label>
                <input type="text" id="in-username" placeholder="e.g. client" required>
            </div>
            <div class="form-group">
                <label for="in-password">Password</label>
                <input type="password" id="in-password" placeholder="••••••••" required>
            </div>
            <button type="submit" class="btn-submit">Authenticate Wallet Desk</button>
        </form>
    </div>
    <div class="toast-container" id="toast-wrap"></div>

    <script>
        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function submitLogin(e) {
            e.preventDefault();
            const username = document.getElementById('in-username').value.trim();
            const password = document.getElementById('in-password').value.trim();
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ username, password, portal: 'client' })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Advertiser verified! Syncing campaigns...');
                    setTimeout(() => { window.location.href = '/client'; }, 1000);
                } else {
                    showToast(data.message || 'Access Denied.', 'error');
                }
            } catch (err) {
                showToast('Authentication network error.', 'error');
            }
        }
    </script>
</body>
</html>
"""

# ─── FRONTEND: ADMIN & DEV LOGIN HTML (EMOJI-FREE) ────────────────────────────

LOGIN_ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Staff Command Suite</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #040507;
            --glass-bg: rgba(18, 16, 22, 0.75);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-purple: #9d4edd;
            --color-purple-hover: #b576f7;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(circle at 50% 15%, rgba(157, 78, 221, 0.15) 0%, transparent 45%),
                radial-gradient(circle at 12% 88%, rgba(239, 71, 111, 0.05) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; overflow: hidden;
        }
        .login-card {
            background: var(--glass-bg);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid var(--glass-border);
            border-radius: 24px;
            padding: 40px;
            width: 100%; max-width: 440px;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
            position: relative;
            animation: cardSlideIn 0.5s ease-out forwards;
        }
        .logo-section { text-align: center; margin-bottom: 30px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #ffc8dd 0%, var(--color-purple) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .logo-sub { font-size: 13px; color: var(--text-secondary); margin-top: 4px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
        input { width: 100%; background: rgba(0, 0, 0, 0.5); border: 1px solid var(--glass-border); border-radius: 10px; padding: 14px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px; transition: all 0.2s ease; }
        input:focus { border-color: var(--color-purple); box-shadow: 0 0 12px rgba(157, 78, 221, 0.25); }
        .btn-submit { width: 100%; background: var(--color-purple); color: #fff; padding: 14px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 15px; font-weight: 700; border: none; cursor: pointer; transition: all 0.2s ease; box-shadow: 0 4px 15px rgba(157, 78, 221, 0.2); margin-top: 10px; }
        .btn-submit:hover { background: var(--color-purple-hover); box-shadow: 0 6px 20px rgba(157, 78, 221, 0.4); transform: translateY(-1px); }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; }
        .toast { background: rgba(18, 16, 22, 0.95); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-purple); padding: 14px 20px; border-radius: 8px; font-size: 13px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
        .toast.error { border-left-color: #ef476f; }
        @keyframes cardSlideIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo-section">
            <span class="logo-text">redditOS</span>
            <div class="logo-sub">Staff Mod Suite Gateway</div>
        </div>

        <form onsubmit="submitLogin(event)">
            <div class="form-group">
                <label for="in-username">Staff Username</label>
                <input type="text" id="in-username" placeholder="e.g. admin" required>
            </div>
            <div class="form-group">
                <label for="in-password">Password</label>
                <input type="password" id="in-password" placeholder="••••••••" required>
            </div>
            <button type="submit" class="btn-submit">Enter Audit Suite</button>
        </form>
    </div>
    <div class="toast-container" id="toast-wrap"></div>

    <script>
        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function submitLogin(e) {
            e.preventDefault();
            const username = document.getElementById('in-username').value.trim();
            const password = document.getElementById('in-password').value.trim();
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ username, password, portal: 'admin' })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Credentials approved! Launching telemetry...');
                    setTimeout(() => { window.location.href = '/admin'; }, 1000);
                } else {
                    showToast(data.message || 'Access Denied.', 'error');
                }
            } catch (err) {
                showToast('Authentication gateway error.', 'error');
            }
        }
    </script>
</body>
</html>
"""

# ─── FRONTEND: DYNAMIC EARNER DASHBOARD HTML (EMOJI-FREE) ─────────────────────

EARNER_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Earner Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #060709;
            --glass-bg: rgba(18, 20, 29, 0.7);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-purple: #9d4edd;
            --color-purple-hover: #b576f7;
            --color-emerald: #06d6a0;
            --color-crimson: #ef476f;
            --color-amber: #ffd166;
            --color-blue: #3b82f6;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: radial-gradient(circle at 12% 18%, rgba(157, 78, 221, 0.15) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            min-height: 100vh;
        }
        header {
            background: rgba(10, 11, 15, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--glass-border);
            position: sticky; top: 0; z-index: 100;
        }
        .nav-container {
            max-width: 1400px; margin: 0 auto; padding: 16px 24px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .logo-wrap { display: flex; align-items: center; gap: 12px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #e0aaff 0%, var(--color-purple) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .portal-role-badge {
            background: rgba(157, 78, 221, 0.15); border: 1px solid rgba(157, 78, 221, 0.3); color: #d8b4fe;
            padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;
        }
        .tabs { display: flex; gap: 8px; }
        .tab-btn {
            background: transparent; border: 1px solid transparent; color: var(--text-secondary);
            padding: 8px 16px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 600;
            border-radius: 8px; cursor: pointer; transition: all 0.2s ease;
        }
        .tab-btn:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.04); }
        .tab-btn.active { color: var(--text-primary); background: rgba(157, 78, 221, 0.15); border-color: rgba(157, 78, 221, 0.3); }
        .user-nav-profile { display: flex; align-items: center; gap: 14px; }
        .nav-profile-name { font-size: 14px; font-weight: 600; }
        .btn-logout { background: transparent; border: 1px solid var(--glass-border); color: var(--color-crimson); padding: 6px 12px; font-size: 12px; font-weight: 700; border-radius: 6px; cursor: pointer; transition: all 0.2s ease; }
        .btn-logout:hover { background: rgba(239, 71, 111, 0.1); border-color: rgba(239, 71, 111, 0.3); }

        main { max-width: 1400px; margin: 0 auto; padding: 32px 24px; }
        .view-content { display: none; animation: fadeIn 0.4s ease forwards; }
        .view-content.active { display: block; }

        .card {
            background: var(--glass-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--glass-border); border-radius: 16px; padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3); transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative; overflow: hidden; margin-bottom: 24px;
        }
        .card:hover { transform: translateY(-2px); border-color: rgba(157, 78, 221, 0.25); box-shadow: 0 12px 40px 0 rgba(157, 78, 221, 0.12); }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }
        .metric-title { color: var(--text-secondary); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }
        .metric-value { font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; color: var(--text-primary); }

        .dashboard-split { display: grid; grid-template-columns: 3fr 2fr; gap: 24px; align-items: start; }
        @media(max-width: 1024px) { .dashboard-split { grid-template-columns: 1fr; } }

        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--glass-border); }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { background: rgba(10, 11, 15, 0.5); padding: 14px 16px; color: var(--text-secondary); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--glass-border); }
        td { padding: 16px; border-bottom: 1px solid var(--glass-border); font-size: 13px; }
        tr:hover td { background: rgba(255, 255, 255, 0.015); }

        .task-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 20px; margin-bottom: 24px; }
        .task-card { background: rgba(10, 12, 18, 0.4); border: 1px solid var(--glass-border); border-radius: 12px; padding: 20px; transition: all 0.2s ease; }
        .task-card:hover { border-color: var(--color-purple); transform: translateY(-2px); box-shadow: 0 4px 15px rgba(157, 78, 221, 0.15); }

        .input-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }
        textarea, input[type="text"], input[type="number"], select { width: 100%; background: rgba(0, 0, 0, 0.4); border: 1px solid var(--glass-border); border-radius: 10px; padding: 12px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px; }
        .upload-dropzone { border: 2px dashed var(--glass-border); border-radius: 12px; padding: 30px; text-align: center; cursor: pointer; background: rgba(0, 0, 0, 0.2); color: var(--text-secondary); margin-bottom: 16px; }
        .upload-dropzone:hover { border-color: var(--color-purple); background: rgba(157, 78, 221, 0.05); color: var(--text-primary); }

        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 20px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 700; cursor: pointer; border: none; text-decoration: none; }
        .btn.primary { background: var(--color-purple); color: #fff; }
        .btn.primary:hover { background: var(--color-purple-hover); box-shadow: 0 0 16px rgba(157, 78, 221, 0.3); }
        .btn.success { background: rgba(6, 214, 160, 0.15); border: 1px solid rgba(6, 214, 160, 0.3); color: var(--color-emerald); }
        .btn.success:hover { background: var(--color-emerald); color: #000; }
        .btn.sm { padding: 6px 12px; font-size: 12px; border-radius: 6px; }

        .badge { padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block; text-transform: uppercase; }
        .badge.pending { background: rgba(255, 209, 102, 0.1); border: 1px solid rgba(255, 209, 102, 0.25); color: var(--color-amber); }
        .badge.completed { background: rgba(6, 214, 160, 0.1); border: 1px solid rgba(6, 214, 160, 0.25); color: var(--color-emerald); }
        .badge.blue { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.25); color: var(--color-blue); }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; }
        .toast { background: rgba(18, 20, 29, 0.9); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-purple); padding: 16px 20px; border-radius: 8px; font-size: 14px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
        .toast.success { border-left-color: var(--color-emerald); }
        .toast.error { border-left-color: var(--color-crimson); }
    </style>
</head>
<body>

    <header>
        <div class="nav-container">
            <div class="logo-wrap">
                <span class="logo-text">redditOS</span>
                <span class="portal-role-badge">Earner Suite</span>
            </div>
            
            <div class="tabs">
                <button class="tab-btn active" id="tab-btn-earner-tasks" onclick="switchTab('earner-tasks')">Bounties Catalog</button>
                <button class="tab-btn" id="tab-btn-earner-ledger" onclick="switchTab('earner-ledger')">Withdrawal Sweeps</button>
            </div>

            <div class="user-nav-profile">
                <span class="nav-profile-name" id="user-display-name">Earner</span>
                <button class="btn-logout" onclick="triggerLogout()">Logout</button>
            </div>
        </div>
    </header>

    <main>
        
        <div id="view-earner-tasks" class="view-content active">
            <div class="metrics-grid">
                <div class="card">
                    <div class="metric-title">Available Reward Balance</div>
                    <div class="metric-value" id="earner-stat-avail">0.00 cr</div>
                </div>
                <div class="card">
                    <div class="metric-title">Pending Holds Balance</div>
                    <div class="metric-value" id="earner-stat-pending">0.00 cr</div>
                </div>
                <div class="card">
                    <div class="metric-title">Earner Trust Rating</div>
                    <div class="metric-value" id="earner-stat-trust">100</div>
                </div>
            </div>

            <!-- Active Claim Box -->
            <div class="card" id="earner-active-claim-box" style="display:none; border-color: rgba(6,214,160,0.3);">
                <h3 style="font-family:'Outfit'; margin-top:0; color:var(--color-emerald)">You Have An Active Task Claim</h3>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px; align-items: start;">
                    <div>
                        <p><strong>Bounty Task ID</strong>: <code id="claim-task-id">ID</code></p>
                        <p><strong>Reward Reward</strong>: <span style="color:var(--color-emerald); font-weight:700" id="claim-task-reward">0 cr</span></p>
                        <p><strong>Target URL</strong>: <a id="claim-task-link" href="#" target="_blank" style="color:var(--color-purple-hover)">Open Reddit Link</a></p>
                        <p><strong>Claim Expiration Countdown</strong>: <span style="font-size:16px; font-weight:700; color:var(--color-crimson)" id="claim-timer">00:00</span></p>
                    </div>
                    <div id="claim-proof-submit-box">
                        <div class="input-group">
                            <label for="txt-claim-proof-url">Reddit Proof Comment URL</label>
                            <input type="text" id="txt-claim-proof-url" placeholder="https://www.reddit.com/r/.../comments/.../c...">
                        </div>
                        
                        <label>Screenshot Proof (Optional)</label>
                        <div class="upload-dropzone" onclick="triggerFileInput()" id="dropzone">
                            Click or Drag & Drop to upload proof screenshot
                            <input type="file" id="file-proof-screenshot" style="display:none" onchange="uploadScreenshot(event)">
                        </div>
                        <input type="hidden" id="hdn-screenshot-url">

                        <button class="btn primary" onclick="submitClaimProof()">Submit Claim Proofs</button>
                    </div>
                </div>
            </div>

            <h3 style="font-family:'Outfit'; margin-top: 30px;">Explore Campaign Bounty Tasks</h3>
            <div class="task-grid" id="earner-task-list">
                <!-- Populated dynamically -->
            </div>
        </div>

        <div id="view-earner-ledger" class="view-content">
            <div class="dashboard-split">
                <!-- Withdrawals list -->
                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-top:0">Sweeps & Withdrawal Requests Ledger</h3>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Amount</th>
                                    <th>Method</th>
                                    <th>Account Details</th>
                                    <th>Sweep Status</th>
                                </tr>
                            </thead>
                            <tbody id="tbl-earner-withdrawals">
                                <!-- Populated dynamically -->
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Form controls -->
                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-top:0">Single-Click Payout Sweep</h3>
                    <div class="input-group">
                        <label for="num-withdraw-amount">Credits to Cash Out</label>
                        <input type="number" id="num-withdraw-amount" min="1" step="any" placeholder="e.g. 50">
                    </div>
                    <div class="input-group">
                        <label for="sel-withdraw-method">Payout Method</label>
                        <select id="sel-withdraw-method" onchange="updateWithdrawDetailsLabel()">
                            <option value="upi">UPI ID</option>
                            <option value="paypal">PayPal Email</option>
                            <option value="crypto">USDT / Crypto Wallet</option>
                        </select>
                    </div>
                    <div class="input-group">
                        <label id="lbl-withdraw-info" for="txt-withdraw-info">UPI Address</label>
                        <input type="text" id="txt-withdraw-info" placeholder="username@bank">
                    </div>
                    <button class="btn success" onclick="requestPayout()">Initiate Payout Sweep</button>

                    <hr style="border:none; border-top:1px solid var(--glass-border); margin: 24px 0">
                    
                    <h4 style="font-family:'Outfit'; margin-top:0">Save Payment Coordinates</h4>
                    <div style="font-size:12px; color:var(--text-secondary); margin-bottom:12px">
                        Save coordinates to bypass forms next time.
                    </div>
                    <div style="display:flex; gap:8px">
                        <button class="btn sm primary" onclick="saveEarnerPaymentDetail('upi')">UPI ID</button>
                        <button class="btn sm primary" onclick="saveEarnerPaymentDetail('paypal')">PayPal Email</button>
                        <button class="btn sm primary" onclick="saveEarnerPaymentDetail('crypto')">USDT Wallet</button>
                    </div>
                </div>
            </div>
        </div>

    </main>

    <div class="toast-container" id="toast-wrap"></div>

    <script>
        let userSession = null;
        let activeClaimTimerInterval = null;

        async function checkSession() {
            try {
                const res = await fetch('/api/user/session');
                const data = await res.json();
                if (!res.ok || !data.logged_in || (data.role !== 'user' && data.role !== 'dev')) {
                    window.location.href = '/login';
                    return;
                }
                userSession = data;
                document.getElementById('user-display-name').innerText = data.display_name;
                loadEarnerDashboard();
            } catch (err) {
                window.location.href = '/login';
            }
        }

        function switchTab(viewName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.view-content').forEach(view => view.classList.remove('active'));
            document.getElementById('tab-btn-' + viewName).classList.add('active');
            document.getElementById('view-' + viewName).classList.add('active');
            loadEarnerDashboard();
        }

        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span><span style="cursor:pointer;margin-left:12px;opacity:0.6" onclick="this.parentElement.remove()">✕</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function triggerLogout() {
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/login';
        }

        async function loadEarnerDashboard() {
            try {
                const res = await fetch('/api/earner/dashboard');
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(data.message);

                document.getElementById('earner-stat-avail').innerText = data.stats.available.toFixed(2) + ' cr';
                document.getElementById('earner-stat-pending').innerText = data.stats.pending.toFixed(2) + ' cr';
                document.getElementById('earner-stat-trust').innerText = data.stats.trust_score;

                const claimBox = document.getElementById('earner-active-claim-box');
                if (data.active_claim) {
                    claimBox.style.display = 'block';
                    document.getElementById('claim-task-id').innerText = data.active_claim.task_id;
                    document.getElementById('claim-task-reward').innerText = data.active_claim.reward.toFixed(2) + ' credits';
                    document.getElementById('claim-task-link').href = data.active_claim.target_url;

                    if (data.active_claim.submitted) {
                        document.getElementById('claim-proof-submit-box').innerHTML = `
                            <div style="background:rgba(6,214,160,0.1); border:1px solid rgba(6,214,160,0.3); padding:16px; border-radius:10px; text-align:center">
                                <span style="font-weight:700; color:var(--color-emerald)">PROOF SUCCESSFULLY SUBMITTED</span>
                                <div style="font-size:12px; color:var(--text-secondary); margin-top:8px">
                                    Liveness audit is currently holding balance in pending state. Status: <strong>${data.active_claim.submission_status.toUpperCase()}</strong>
                                </div>
                            </div>
                        `;
                        clearInterval(activeClaimTimerInterval);
                    } else {
                        startActiveClaimTimer(data.active_claim.expires_at);
                    }
                } else {
                    claimBox.style.display = 'none';
                    clearInterval(activeClaimTimerInterval);
                }

                const listWrap = document.getElementById('earner-task-list');
                if (data.tasks.length === 0) {
                    listWrap.innerHTML = `<div style="text-align:center; color:var(--text-secondary); grid-column:1/-1; padding:30px">No active campaign tasks available at the moment. Browse Discord for updates!</div>`;
                } else {
                    listWrap.innerHTML = data.tasks.map(t => `
                        <div class="task-card">
                            <div style="display:flex; justify-content:space-between; align-items:start; margin-bottom:12px">
                                <span class="badge blue">${t.type.toUpperCase().replace('_', ' ')}</span>
                                <span style="color:var(--color-emerald); font-weight:700; font-size:16px">+${t.reward.toFixed(2)} cr</span>
                            </div>
                            <div style="font-size:12px; color:var(--text-secondary); line-height:1.6; margin-bottom:16px">
                                Target Link: <a href="${t.target_url}" target="_blank" style="color:var(--color-purple-hover)">Open</a><br>
                                Time Limit: <strong>${t.time_limit} minutes</strong><br>
                                Trust Required: <strong>${t.min_trust}</strong><br>
                                Slots Filled: <strong>${t.slots_filled}/${t.slots_total} filled</strong>
                            </div>
                            <button class="btn primary sm" style="width:100%" onclick="claimBountyTask('${t.task_id}')" ${data.active_claim ? 'disabled' : ''}>Claim Bounty Slot</button>
                        </div>
                    `).join('');
                }

                const tblWithdraw = document.getElementById('tbl-earner-withdrawals');
                if (data.withdrawals.length === 0) {
                    tblWithdraw.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-secondary)">No withdrawal histories on record.</td></tr>`;
                } else {
                    tblWithdraw.innerHTML = data.withdrawals.map(w => `
                        <tr>
                            <td><strong>#${w.withdrawal_id}</strong></td>
                            <td><span style="color:var(--color-emerald); font-weight:700">+${w.amount.toFixed(2)} cr</span></td>
                            <td><span class="badge blue">${w.payment_method.toUpperCase()}</span></td>
                            <td><code>${w.payment_info}</code></td>
                            <td><span class="badge ${w.status === 'completed' ? 'completed' : w.status === 'pending' ? 'pending' : 'flagged'}">${w.status.toUpperCase()}</span></td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error(err);
            }
        }

        function startActiveClaimTimer(expireIsoStr) {
            clearInterval(activeClaimTimerInterval);
            const targetTime = new Date(expireIsoStr).getTime();
            function tick() {
                const now = new Date().getTime();
                const diff = targetTime - now;
                if (diff <= 0) {
                    document.getElementById('claim-timer').innerText = 'EXPIRED';
                    clearInterval(activeClaimTimerInterval);
                    loadEarnerDashboard();
                    return;
                }
                const mins = Math.floor(diff / 60000);
                const secs = Math.floor((diff % 60000) / 1000);
                document.getElementById('claim-timer').innerText = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
            }
            tick();
            activeClaimTimerInterval = setInterval(tick, 1000);
        }

        async function claimBountyTask(taskId) {
            const res = await fetch('/api/earner/claim', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ task_id: taskId })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showToast(data.message, 'success');
                loadEarnerDashboard();
            } else {
                showToast(data.message || 'Slot claim error.', 'error');
            }
        }

        function triggerFileInput() { document.getElementById('file-proof-screenshot').click(); }

        async function uploadScreenshot(e) {
            const file = e.target.files[0];
            if (!file) return;
            showToast("Uploading screenshot to server...", "info");
            const fd = new FormData();
            fd.append('file', file);
            try {
                const res = await fetch('/api/proof/upload', { method: 'POST', body: fd });
                const data = await res.json();
                if (res.ok && data.url) {
                    document.getElementById('hdn-screenshot-url').value = data.url;
                    document.getElementById('dropzone').innerText = file.name + ' uploaded successfully!';
                    showToast('Screenshot uploaded! Complete proof by pasting URL.', 'success');
                } else {
                    showToast(data.message || 'File upload failed.', 'error');
                }
            } catch (err) {
                showToast('Screenshot upload failed.', 'error');
            }
        }

        async function submitClaimProof() {
            const proofUrl = document.getElementById('txt-claim-proof-url').value.trim();
            const screenshotUrl = document.getElementById('hdn-screenshot-url').value;
            if (!proofUrl) {
                showToast('Please paste the Reddit comment proof link.', 'error');
                return;
            }
            try {
                const res = await fetch('/api/earner/dashboard');
                const state = await res.json();
                if (!res.ok || !state.active_claim) throw new Error("No active claim");
                const c_id = state.active_claim.claim_id;

                const submitRes = await fetch('/api/earner/submit', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ claim_id: c_id, proof_url: proofUrl, screenshot_url: screenshotUrl || null })
                });
                const submitData = await submitRes.json();
                if (submitRes.ok && submitData.success) {
                    showToast(submitData.message, 'success');
                    loadEarnerDashboard();
                } else {
                    showToast(submitData.message || 'Proof submission failed.', 'error');
                }
            } catch (err) {
                showToast('Error sending proof.', 'error');
            }
        }

        function updateWithdrawDetailsLabel() {
            const method = document.getElementById('sel-withdraw-method').value;
            const lbl = document.getElementById('lbl-withdraw-info');
            const inInfo = document.getElementById('txt-withdraw-info');
            if (method === 'upi') { lbl.innerText = 'UPI Address'; inInfo.placeholder = 'username@bank'; }
            else if (method === 'paypal') { lbl.innerText = 'PayPal Email Address'; inInfo.placeholder = 'you@example.com'; }
            else { lbl.innerText = 'USDT Wallet / Exchange Pay ID (e.g. TRC20)'; inInfo.placeholder = 'TY... or Pay ID'; }
        }

        async function requestPayout() {
            const amount = parseFloat(document.getElementById('num-withdraw-amount').value);
            const method = document.getElementById('sel-withdraw-method').value;
            const info = document.getElementById('txt-withdraw-info').value.trim();
            if (isNaN(amount) || amount <= 0 || !info) {
                showToast('Please specify amount and coordinates.', 'error');
                return;
            }
            const res = await fetch('/api/earner/withdraw', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ amount, method, info })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showToast(data.message, 'success');
                document.getElementById('num-withdraw-amount').value = '';
                document.getElementById('txt-withdraw-info').value = '';
                loadEarnerDashboard();
            } else {
                showToast(data.message || 'Withdrawal sweep failed.', 'error');
            }
        }

        async function saveEarnerPaymentDetail(method) {
            const value = prompt(`Enter your payment coordinates for ${method.toUpperCase()}:`);
            if (!value) return;
            let network = null;
            if (method === 'crypto') { network = prompt('Specify blockchain network (e.g. TRC20, ERC20):'); }
            const res = await fetch('/api/earner/profile', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ method, value, network })
            });
            const data = await res.json();
            if (res.ok && data.success) {
                showToast(data.message, 'success');
                loadEarnerDashboard();
            } else {
                showToast(data.message, 'error');
            }
        }

        checkSession();
        setInterval(() => { if (userSession) loadEarnerDashboard(); }, 20000);
    </script>
</body>
</html>
"""

# ─── FRONTEND: DYNAMIC CLIENT DASHBOARD HTML (EMOJI-FREE) ─────────────────────

CLIENT_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Client Campaigns</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #050608;
            --glass-bg: rgba(16, 22, 20, 0.75);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-emerald: #06d6a0;
            --color-emerald-hover: #34e8ba;
            --color-purple: #9d4edd;
            --color-purple-hover: #b576f7;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: radial-gradient(circle at 88% 18%, rgba(6, 214, 160, 0.1) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            min-height: 100vh;
        }
        header {
            background: rgba(10, 11, 15, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--glass-border);
            position: sticky; top: 0; z-index: 100;
        }
        .nav-container {
            max-width: 1400px; margin: 0 auto; padding: 16px 24px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .logo-wrap { display: flex; align-items: center; gap: 12px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #a7f3d0 0%, var(--color-emerald) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .portal-role-badge {
            background: rgba(6, 214, 160, 0.15); border: 1px solid rgba(6, 214, 160, 0.3); color: var(--color-emerald);
            padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;
        }
        .tabs { display: flex; gap: 8px; }
        .tab-btn {
            background: transparent; border: 1px solid transparent; color: var(--text-secondary);
            padding: 8px 16px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 600;
            border-radius: 8px; cursor: pointer; transition: all 0.2s ease;
        }
        .tab-btn:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.04); }
        .tab-btn.active { color: var(--text-primary); background: rgba(6, 214, 160, 0.15); border-color: rgba(6, 214, 160, 0.3); }
        .user-nav-profile { display: flex; align-items: center; gap: 14px; }
        .nav-profile-name { font-size: 14px; font-weight: 600; }
        .btn-logout { background: transparent; border: 1px solid var(--glass-border); color: #ef476f; padding: 6px 12px; font-size: 12px; font-weight: 700; border-radius: 6px; cursor: pointer; }
        .btn-logout:hover { background: rgba(239, 71, 111, 0.1); border-color: rgba(239, 71, 111, 0.3); }

        main { max-width: 1400px; margin: 0 auto; padding: 32px 24px; }
        .view-content { display: none; animation: fadeIn 0.4s ease forwards; }
        .view-content.active { display: block; }

        .card {
            background: var(--glass-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--glass-border); border-radius: 16px; padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4); margin-bottom: 24px;
        }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }
        .metric-title { color: var(--text-secondary); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 8px; }
        .metric-value { font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; color: var(--text-primary); }

        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--glass-border); }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { background: rgba(10, 11, 15, 0.5); padding: 14px 16px; color: var(--text-secondary); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--glass-border); }
        td { padding: 16px; border-bottom: 1px solid var(--glass-border); font-size: 13px; }

        .input-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-size: 12px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; }
        textarea { width: 100%; background: rgba(0, 0, 0, 0.4); border: 1px solid var(--glass-border); border-radius: 10px; padding: 12px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; font-size: 14px; resize: vertical; min-height: 160px; }
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 20px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 700; cursor: pointer; border: none; }
        .btn.primary { background: var(--color-emerald); color: #000; }
        .btn.primary:hover { background: var(--color-emerald-hover); box-shadow: 0 0 16px rgba(6, 214, 160, 0.3); }

        .badge { padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block; text-transform: uppercase; }
        .badge.completed { background: rgba(6, 214, 160, 0.1); border: 1px solid rgba(6, 214, 160, 0.25); color: var(--color-emerald); }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; }
        .toast { background: rgba(16, 22, 20, 0.9); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-emerald); padding: 16px 20px; border-radius: 8px; font-size: 14px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
    </style>
</head>
<body>

    <header>
        <div class="nav-container">
            <div class="logo-wrap">
                <span class="logo-text">redditOS</span>
                <span class="portal-role-badge">Advertiser Desk</span>
            </div>
            
            <div class="tabs">
                <button class="tab-btn active" id="tab-btn-client-campaigns" onclick="switchTab('client-campaigns')">Campaigns Analytics</button>
                <button class="tab-btn" id="tab-btn-client-seeder" onclick="switchTab('client-seeder')">Seeder Desk</button>
            </div>

            <div class="user-nav-profile">
                <span class="nav-profile-name" id="user-display-name">Client</span>
                <button class="btn-logout" onclick="triggerLogout()">Logout</button>
            </div>
        </div>
    </header>

    <main>
        
        <div id="view-client-campaigns" class="view-content active">
            <div class="metrics-grid">
                <div class="card">
                    <div class="metric-title">Active Target Campaigns</div>
                    <div class="metric-value" id="client-stat-campaigns">0</div>
                </div>
                <div class="card">
                    <div class="metric-title">Advertiser Spent Capital</div>
                    <div class="metric-value" id="client-stat-spent">0.00 cr</div>
                </div>
                <div class="card">
                    <div class="metric-title">Wallet Balance Funded</div>
                    <div class="metric-value" id="client-stat-balance">$0.00</div>
                </div>
            </div>

            <div class="card">
                <h3 style="font-family:'Outfit'; margin-top:0">Campaigns Engagement Metrics</h3>
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Campaign ID</th>
                                <th>Subreddit Target</th>
                                <th>Title Keyword</th>
                                <th>Live Link</th>
                                <th>Seeding Comments</th>
                                <th>Slots Filled</th>
                                <th>Liveness Status</th>
                            </tr>
                        </thead>
                        <tbody id="tbl-client-campaigns">
                            <!-- Populated dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="view-client-seeder" class="view-content">
            <div class="card" style="max-width: 900px; margin: 0 auto;">
                <h3 style="font-family: 'Outfit'; margin-top: 0;">Direct Campaign Seeder Tool</h3>
                <p style="font-size: 14px; color: var(--text-secondary); margin-bottom: 24px;">
                    Paste hierarchical structured campaign seeding text directly into the textbox block. The parser engine will dynamically deploy the parent post task and schedule all nested child comments automatically.
                </p>
                <div class="input-group">
                    <label for="txt-client-seeder-box">Structured Campaign Seeder Template</label>
                    <textarea id="txt-client-seeder-box" placeholder="Post 2 - LLM Gateway V2&#10;Keyword : llm pricing&#10;Subreddit : r/LLMDevs&#10;Title : LLM gateway model swaps and pricing&#10;Content : My provider switching workflow...&#10;..."></textarea>
                </div>
                <div style="display: flex; justify-content: flex-end;">
                    <button class="btn primary" id="btn-client-deploy" onclick="deployClientCampaign()">Deploy Campaign Tasks</button>
                </div>
            </div>
        </div>

    </main>

    <div class="toast-container" id="toast-wrap"></div>

    <script>
        let userSession = null;
        async function checkSession() {
            try {
                const res = await fetch('/api/user/session');
                const data = await res.json();
                if (!res.ok || !data.logged_in || (data.role !== 'client' && data.role !== 'dev')) {
                    window.location.href = '/client/login';
                    return;
                }
                userSession = data;
                document.getElementById('user-display-name').innerText = data.display_name;
                loadClientDashboard();
            } catch (err) {
                window.location.href = '/client/login';
            }
        }

        function switchTab(viewName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.view-content').forEach(view => view.classList.remove('active'));
            document.getElementById('tab-btn-' + viewName).classList.add('active');
            document.getElementById('view-' + viewName).classList.add('active');
            loadClientDashboard();
        }

        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast`;
            toast.innerHTML = `<span>${message}</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function triggerLogout() {
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/client/login';
        }

        async function loadClientDashboard() {
            try {
                const res = await fetch('/api/client/dashboard');
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(data.message);

                document.getElementById('client-stat-campaigns').innerText = data.stats.total_campaigns;
                document.getElementById('client-stat-spent').innerText = data.stats.total_spent.toFixed(2) + ' cr';
                document.getElementById('client-stat-balance').innerText = '$' + data.stats.wallet_balance.toFixed(2);

                const tbl = document.getElementById('tbl-client-campaigns');
                if (data.campaigns.length === 0) {
                    tbl.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-secondary)">No campaigns created yet.</td></tr>`;
                } else {
                    tbl.innerHTML = data.campaigns.map(c => `
                        <tr>
                            <td><strong>${c.campaign_id}</strong></td>
                            <td><code>r/${c.subreddit}</code></td>
                            <td style="font-weight:600">${c.title.slice(0,30)}...</td>
                            <td>
                                ${c.target_post_url ? `<a href="${c.target_post_url}" target="_blank" style="color:var(--color-emerald); font-weight:700">Open Link</a>` : `<span style="opacity:0.5">Post URL Unlinked</span>`}
                            </td>
                            <td>${c.comment_count} seedings</td>
                            <td><strong>${c.slots_filled}/${c.slots_total} claimed</strong></td>
                            <td><span class="badge completed">${c.status.toUpperCase()}</span></td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error(err);
            }
        }

        async function deployClientCampaign() {
            const content = document.getElementById('txt-client-seeder-box').value.trim();
            if (!content) {
                showToast('Please paste structured seeder content first.', 'error');
                return;
            }
            const btn = document.getElementById('btn-client-deploy');
            btn.disabled = true;
            btn.innerText = 'Deploying...';
            try {
                const res = await fetch('/api/campaign/import', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ content })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast('Campaign parsed and deployed!', 'success');
                    document.getElementById('txt-client-seeder-box').value = '';
                    switchTab('client-campaigns');
                } else {
                    showToast(data.message || 'Seeding format error.', 'error');
                }
            } catch (err) {
                showToast('Import seeder router offline.', 'error');
            } finally {
                btn.disabled = false;
                btn.innerText = 'Deploy Campaign Tasks';
            }
        }

        checkSession();
        setInterval(() => { if (userSession) loadClientDashboard(); }, 20000);
    </script>
</body>
</html>
"""

# ─── FRONTEND: DYNAMIC ADMIN DASHBOARD HTML (EMOJI-FREE) ─────────────────────

ADMIN_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>redditOS — Staff Command Suite</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://unpkg.com/tesseract.js@5.0.5/dist/tesseract.min.js"></script>
    <style>
        :root {
            --bg-base: #040507;
            --glass-bg: rgba(18, 16, 22, 0.75);
            --glass-border: rgba(255, 255, 255, 0.08);
            --color-purple: #9d4edd;
            --color-purple-hover: #b576f7;
            --color-emerald: #06d6a0;
            --color-crimson: #ef476f;
            --color-amber: #ffd166;
            --color-blue: #3b82f6;
            --text-primary: #f3f4f6;
            --text-secondary: #9ca3af;
        }
        * { box-sizing: border-box; outline: none; }
        body {
            background-color: var(--bg-base);
            background-image: radial-gradient(circle at 12% 18%, rgba(157, 78, 221, 0.15) 0%, transparent 40%);
            background-attachment: fixed;
            color: var(--text-primary);
            font-family: 'Inter', sans-serif;
            margin: 0; padding: 0;
            min-height: 100vh;
        }
        header {
            background: rgba(10, 11, 15, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-bottom: 1px solid var(--glass-border);
            position: sticky; top: 0; z-index: 100;
        }
        .nav-container {
            max-width: 1400px; margin: 0 auto; padding: 16px 24px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .logo-wrap { display: flex; align-items: center; gap: 12px; }
        .logo-text {
            font-family: 'Outfit', sans-serif; font-size: 24px; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(135deg, #ffc8dd 0%, var(--color-purple) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .portal-role-badge {
            background: rgba(157, 78, 221, 0.15); border: 1px solid rgba(157, 78, 221, 0.3); color: #ffc8dd;
            padding: 4px 10px; border-radius: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.8px;
        }
        .tabs { display: flex; gap: 8px; }
        .tab-btn {
            background: transparent; border: 1px solid transparent; color: var(--text-secondary);
            padding: 8px 16px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 600;
            border-radius: 8px; cursor: pointer; transition: all 0.2s ease;
        }
        .tab-btn:hover { color: var(--text-primary); background: rgba(255, 255, 255, 0.04); }
        .tab-btn.active { color: var(--text-primary); background: rgba(157, 78, 221, 0.15); border-color: rgba(157, 78, 221, 0.3); }
        .user-nav-profile { display: flex; align-items: center; gap: 14px; }
        .nav-profile-name { font-size: 14px; font-weight: 600; }
        .btn-logout { background: transparent; border: 1px solid var(--glass-border); color: #ef476f; padding: 6px 12px; font-size: 12px; font-weight: 700; border-radius: 6px; cursor: pointer; }

        main { max-width: 1400px; margin: 0 auto; padding: 32px 24px; }
        .view-content { display: none; animation: fadeIn 0.4s ease forwards; }
        .view-content.active { display: block; }

        .card {
            background: var(--glass-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--glass-border); border-radius: 16px; padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.4); margin-bottom: 24px;
        }
        .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 32px; }
        .metric-title { color: var(--text-secondary); font-size: 12px; font-weight: 700; text-transform: uppercase; }
        .metric-value { font-family: 'Outfit', sans-serif; font-size: 32px; font-weight: 800; color: var(--text-primary); }

        .dashboard-split { display: grid; grid-template-columns: 3fr 2fr; gap: 24px; }
        @media(max-width: 1024px) { .dashboard-split { grid-template-columns: 1fr; } }

        .table-wrap { overflow-x: auto; border-radius: 12px; border: 1px solid var(--glass-border); }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th { background: rgba(10, 11, 15, 0.5); padding: 14px 16px; color: var(--text-secondary); font-size: 11px; font-weight: 700; border-bottom: 1px solid var(--glass-border); }
        td { padding: 16px; border-bottom: 1px solid var(--glass-border); font-size: 13px; }

        .input-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-size: 12px; font-weight: 600; color: var(--text-secondary); }
        textarea { width: 100%; background: rgba(0, 0, 0, 0.4); border: 1px solid var(--glass-border); border-radius: 10px; padding: 12px 16px; color: var(--text-primary); font-family: 'Inter', sans-serif; }
        
        .btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 20px; border-radius: 10px; font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 700; cursor: pointer; border: none; }
        .btn.primary { background: var(--color-purple); color: #fff; }
        .btn.success { background: rgba(6, 214, 160, 0.15); border: 1px solid rgba(6, 214, 160, 0.3); color: var(--color-emerald); }
        .btn.danger { background: rgba(239, 71, 111, 0.15); border: 1px solid rgba(239, 71, 111, 0.3); color: var(--color-crimson); }
        .btn.sm { padding: 6px 12px; font-size: 12px; border-radius: 6px; }

        .badge { padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block; text-transform: uppercase; }
        .badge.pending { background: rgba(255, 209, 102, 0.1); border: 1px solid rgba(255, 209, 102, 0.25); color: var(--color-amber); }
        .badge.completed { background: rgba(6, 214, 160, 0.1); border: 1px solid rgba(6, 214, 160, 0.25); color: var(--color-emerald); }
        .badge.flagged { background: rgba(239, 71, 111, 0.1); border: 1px solid rgba(239, 71, 111, 0.25); color: var(--color-crimson); }
        .badge.blue { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.25); color: var(--color-blue); }

        .sub-nav { display: flex; gap: 8px; margin-bottom: 24px; border-bottom: 1px solid var(--glass-border); padding-bottom: 12px; }
        .sub-tab-btn { background: transparent; border: none; color: var(--text-secondary); font-size: 13px; font-weight: 600; padding: 6px 12px; cursor: pointer; }
        .sub-tab-btn.active { background: rgba(255, 255, 255, 0.05); color: var(--color-purple-hover); }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        .toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 1000; display: flex; flex-direction: column; gap: 10px; }
        .toast { background: rgba(18, 20, 29, 0.9); border: 1px solid var(--glass-border); border-left: 4px solid var(--color-purple); padding: 16px 20px; border-radius: 8px; font-size: 14px; min-width: 300px; display: flex; justify-content: space-between; align-items: center; }
    </style>
</head>
<body>

    <header>
        <div class="nav-container">
            <div class="logo-wrap">
                <span class="logo-text">redditOS</span>
                <span class="portal-role-badge" id="role-indicator">Staff Suite</span>
            </div>
            
            <div class="tabs">
                <button class="tab-btn active" id="tab-btn-admin-dashboard" onclick="switchTab('admin-dashboard')">Payout Sweeps</button>
                <button class="tab-btn" id="tab-btn-admin-earners" onclick="switchTab('admin-earners')">Earner Profiles</button>
                <button class="tab-btn" id="tab-btn-admin-auditor" onclick="switchTab('tab-btn-admin-auditor')">Bulk Auditor</button>
                <button class="tab-btn" id="tab-btn-admin-telemetry" onclick="switchTab('admin-telemetry')">Telemetry</button>
            </div>

            <div class="user-nav-profile">
                <span class="nav-profile-name" id="user-display-name">Admin</span>
                <button class="btn-logout" onclick="triggerLogout()">Logout</button>
            </div>
        </div>
    </header>

    <main>
        
        <div id="view-admin-dashboard" class="view-content active">
            <div class="metrics-grid">
                <div class="card">
                    <div class="metric-title">Active Campaigns</div>
                    <div class="metric-value" id="stat-campaigns">0</div>
                </div>
                <div class="card">
                    <div class="metric-title">Total Earners</div>
                    <div class="metric-value" id="stat-earners">0</div>
                </div>
                <div class="card">
                    <div class="metric-title">Pending Withdrawals</div>
                    <div class="metric-value" id="stat-pending-withdrawals">0.00 cr</div>
                </div>
                <div class="card">
                    <div class="metric-title">Total Completed Paid</div>
                    <div class="metric-value" id="stat-total-paid">0.00 cr</div>
                </div>
            </div>

            <div class="dashboard-split">
                <!-- Sweeps -->
                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-top:0">Payouts Awaiting Action Sweeps</h3>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>ID</th>
                                    <th>Discord User</th>
                                    <th>Amount</th>
                                    <th>Method</th>
                                    <th>Details</th>
                                    <th style="text-align:right">Action</th>
                                </tr>
                            </thead>
                            <tbody id="tbl-pending-withdrawals">
                                <!-- Populated dynamically -->
                            </tbody>
                        </table>
                    </div>
                </div>

                <div class="card">
                    <h3 style="font-family:'Outfit'; margin-top:0">Platform Health & Logs</h3>
                    <div style="font-size: 14px; line-height: 1.6; color: var(--text-secondary);">
                        <p>Welcome to the <strong>redditOS Admin Command Desk</strong>. This browser portal is connected to the live PostgreSQL backend database.</p>
                        <hr style="border: none; border-top: 1px solid var(--glass-border); margin: 16px 0;">
                        <p><strong>Payout Security Hook</strong>:<br>
                        Clicking "Pay" initiates balances deductions inside Neon PostgreSQL, logs transaction coordinates, and triggers an automated guild DMs confirmation sweep.</p>
                        <p><strong>Cookie Session Status</strong>: <code>Live Reddit Session Active</code></p>
                    </div>
                </div>
            </div>
        </div>

        <div id="view-admin-earners" class="view-content">
            <div class="card">
                <h3 style="font-family:'Outfit'; margin-top:0">Verified Earner Profiles</h3>
                <div class="table-wrap">
                    <table>
                        <thead>
                            <tr>
                                <th>Discord ID</th>
                                <th>Reddit Profile</th>
                                <th>Trust Score</th>
                                <th>Available Bal</th>
                                <th>Pending Bal</th>
                                <th>Status</th>
                                <th style="text-align:right">Moderate</th>
                            </tr>
                        </thead>
                        <tbody id="tbl-earners">
                            <!-- Populated dynamically -->
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <div id="view-admin-auditor" class="view-content">
            <div class="card">
                <h3 style="font-family: 'Outfit'; margin-top: 0; margin-bottom: 8px;">Reddit High-Speed Bulk Auditor</h3>
                <p style="font-size: 14px; color: var(--text-secondary); margin-bottom: 24px;">
                    Instantly verify profile existence, karma bounds, and comment liveness parameters concurrently.
                </p>
                <div class="sub-nav">
                    <button class="sub-tab-btn active" id="sub-tab-user" onclick="switchSubAuditor('user')">Bulk User Checker</button>
                    <button class="sub-tab-btn" id="sub-tab-post" onclick="switchSubAuditor('post')">Bulk Post Liveness</button>
                    <button class="sub-tab-btn" id="sub-tab-comment" onclick="switchSubAuditor('comment')">Bulk Comment Validator</button>
                </div>

                <!-- User Audit -->
                <div id="auditor-sub-user" class="auditor-sub-view" style="display: block;">
                    <div class="input-group">
                        <label for="txt-bulk-users">Paste Reddit Usernames (One per line)</label>
                        <textarea id="txt-bulk-users" placeholder="spez&#10;reddit"></textarea>
                    </div>
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 24px;">
                        <button class="btn primary" onclick="runUserAudit()">Run User Audit</button>
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>Reddit Account</th>
                                    <th>Status</th>
                                    <th>Total Karma</th>
                                    <th>Account Age (Days)</th>
                                    <th>Linked Earner Profile</th>
                                </tr>
                            </thead>
                            <tbody id="tbl-audit-users">
                                <tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 16px;">Paste names and click Run User Audit.</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Post Audit -->
                <div id="auditor-sub-post" class="auditor-sub-view" style="display: none;">
                    <div class="input-group">
                        <label for="txt-bulk-posts">Paste Post URLs (One per line)</label>
                        <textarea id="txt-bulk-posts" placeholder="https://www.reddit.com/..."></textarea>
                    </div>
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 24px;">
                        <button class="btn primary" onclick="runPostAudit()">Verify Post Liveness</button>
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>Post Link</th>
                                    <th>Author</th>
                                    <th>Subreddit</th>
                                    <th>Title Keyword</th>
                                    <th>Liveness Status</th>
                                </tr>
                            </thead>
                            <tbody id="tbl-audit-posts">
                                <tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 16px;">Paste links and click Verify Post Liveness.</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Comment Audit -->
                <div id="auditor-sub-comment" class="auditor-sub-view" style="display: none;">
                    <div class="input-group">
                        <label for="txt-bulk-comments">Paste Comment URLs (One per line)</label>
                        <textarea id="txt-bulk-comments" placeholder="https://www.reddit.com/.../..."></textarea>
                    </div>
                    <div style="display: flex; justify-content: flex-end; margin-bottom: 24px;">
                        <button class="btn primary" onclick="runCommentAudit()">Validate Comments</button>
                    </div>
                    <div class="table-wrap">
                        <table>
                            <thead>
                                <tr>
                                    <th>Comment Link</th>
                                    <th>Author</th>
                                    <th>Subreddit</th>
                                    <th>Body Snippet</th>
                                    <th>Liveness Status</th>
                                </tr>
                            </thead>
                            <tbody id="tbl-audit-comments">
                                <tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 16px;">Paste links and click Validate Comments.</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div id="view-admin-telemetry" class="view-content">
            <div class="card">
                <h3 style="font-family:'Outfit'; margin-top:0">Developer & Infrastructure Telemetry Suite</h3>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:30px; margin-top:20px;">
                    <div>
                        <h4 style="font-family:'Outfit'; margin-top:0">Dynamic Real-time Infrastructure Metrics</h4>
                        <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-bottom:20px;">
                            <div style="background:rgba(0,0,0,0.3); border:1px solid var(--glass-border); padding:16px; border-radius:10px; text-align:center">
                                <div style="font-size:12px; color:var(--text-secondary)">CPU Utilization</div>
                                <div style="font-size:28px; font-weight:800; color:var(--color-purple-hover)" id="tel-cpu">0.0%</div>
                            </div>
                            <div style="background:rgba(0,0,0,0.3); border:1px solid var(--glass-border); padding:16px; border-radius:10px; text-align:center">
                                <div style="font-size:12px; color:var(--text-secondary)">Memory Util</div>
                                <div style="font-size:28px; font-weight:800; color:var(--color-blue)" id="tel-mem">0.0%</div>
                            </div>
                            <div style="background:rgba(0,0,0,0.3); border:1px solid var(--glass-border); padding:16px; border-radius:10px; text-align:center">
                                <div style="font-size:12px; color:var(--text-secondary)">DB Latency</div>
                                <div style="font-size:28px; font-weight:800; color:var(--color-emerald)" id="tel-db">0.52 ms</div>
                            </div>
                            <div style="background:rgba(0,0,0,0.3); border:1px solid var(--glass-border); padding:16px; border-radius:10px; text-align:center">
                                <div style="font-size:12px; color:var(--text-secondary)">WS Telemetry Latency</div>
                                <div style="font-size:28px; font-weight:800; color:var(--color-amber)">12.1 ms</div>
                            </div>
                        </div>
                    </div>

                    <div>
                        <h4 style="font-family:'Outfit'; margin-top:0">Performance Load Analysis</h4>
                        <div style="width: 100%; height: 200px; background:rgba(0,0,0,0.2); border:1px solid var(--glass-border); border-radius:10px; display:flex; align-items:center; justify-content:center">
                            <canvas id="telemetryChart" style="max-height: 180px;"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        </div>

    </main>

    <div class="toast-container" id="toast-wrap"></div>

    <script>
        let userSession = null;
        let telemetryChart = null;

        async function checkSession() {
            try {
                const res = await fetch('/api/user/session');
                const data = await res.json();
                if (!res.ok || !data.logged_in || !['staff','admin','dev'].includes(data.role)) {
                    window.location.href = '/admin/login';
                    return;
                }
                userSession = data;
                document.getElementById('user-display-name').innerText = data.display_name;
                document.getElementById('role-indicator').innerText = data.role.toUpperCase() + ' SUITE';
                loadAdminDashboard();
                initTelemetryChart();
            } catch (err) {
                window.location.href = '/admin/login';
            }
        }

        function switchTab(viewName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.view-content').forEach(view => view.classList.remove('active'));
            document.getElementById('tab-btn-' + viewName).classList.add('active');
            document.getElementById('view-' + viewName).classList.add('active');
            loadAdminDashboard();
        }

        function showToast(message, type = 'success') {
            const wrap = document.getElementById('toast-wrap');
            const toast = document.createElement('div');
            toast.className = `toast ${type}`;
            toast.innerHTML = `<span>${message}</span><span style="cursor:pointer;margin-left:12px;opacity:0.6" onclick="this.parentElement.remove()">✕</span>`;
            wrap.appendChild(toast);
            setTimeout(() => { toast.remove(); }, 5000);
        }

        async function triggerLogout() {
            await fetch('/api/logout', { method: 'POST' });
            window.location.href = '/admin/login';
        }

        async function loadAdminDashboard() {
            try {
                const res = await fetch('/api/data');
                const data = await res.json();
                if (!res.ok || !data.success) throw new Error(data.message);

                document.getElementById('stat-campaigns').innerText = data.stats.total_campaigns;
                document.getElementById('stat-earners').innerText = data.stats.total_users;
                document.getElementById('stat-pending-withdrawals').innerText = data.stats.pending_withdrawals_volume.toFixed(2) + ' cr';
                document.getElementById('stat-total-paid').innerText = data.stats.completed_withdrawals_volume.toFixed(2) + ' cr';

                const tblPending = document.getElementById('tbl-pending-withdrawals');
                if (data.withdrawals.length === 0) {
                    tblPending.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:24px; color:var(--text-secondary)">Withdrawal queue completely swept and processed</td></tr>`;
                } else {
                    tblPending.innerHTML = data.withdrawals.map(w => `
                        <tr>
                            <td><strong>#${w.withdrawal_id}</strong></td>
                            <td><span style="font-weight:600">${w.discord_username || 'Discord ID: ' + w.discord_id}</span></td>
                            <td><span style="color:var(--color-emerald); font-weight:800">+${w.amount.toFixed(2)} cr</span></td>
                            <td><span class="badge blue">${w.payment_method.toUpperCase()}</span></td>
                            <td><code style="background:rgba(0,0,0,0.3); padding:4px 8px; border-radius:4px">${w.payment_info}</code></td>
                            <td style="text-align:right">
                                <div style="display:flex; gap:6px; justify-content:flex-end">
                                    <button class="btn success sm" onclick="processPayout(${w.withdrawal_id}, 'approve')">Pay</button>
                                    <button class="btn danger sm" onclick="processPayout(${w.withdrawal_id}, 'reject')">Reject</button>
                                </div>
                            </td>
                        </tr>
                    `).join('');
                }

                const tblEarners = document.getElementById('tbl-earners');
                if (data.users.length === 0) {
                    tblEarners.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:16px; color:var(--text-secondary)">No earners registered.</td></tr>`;
                } else {
                    tblEarners.innerHTML = data.users.map(u => `
                        <tr>
                            <td><strong>${u.discord_username || 'ID: ' + u.discord_id}</strong></td>
                            <td>
                                ${u.reddit_username ? `<a href="https://reddit.com/user/${u.reddit_username}" target="_blank" style="color:var(--color-purple-hover)">u/${u.reddit_username}</a>` : `<span style="opacity:0.4">Unlinked</span>`}
                            </td>
                            <td>${u.trust_score}</td>
                            <td><span style="color:var(--color-emerald); font-weight:700">${u.balance_available.toFixed(2)} cr</span></td>
                            <td><span style="color:var(--color-amber); font-weight:600">${u.balance_pending.toFixed(2)} cr</span></td>
                            <td>${u.verified ? `<span class="badge completed">Verified</span>` : `<span class="badge pending">Awaiting</span>`}</td>
                            <td style="text-align:right">
                                ${u.is_flagged ? `
                                    <button class="btn success sm" onclick="toggleUserFlag('${u.discord_id}', false)">Restore Account</button>
                                ` : `
                                    <button class="btn danger sm" onclick="toggleUserFlag('${u.discord_id}', true)">Flag Earner</button>
                                `}
                            </td>
                        </tr>
                    `).join('');
                }

                document.getElementById('tel-cpu').innerText = data.stats.cpu_usage.toFixed(1) + '%';
                document.getElementById('tel-mem').innerText = data.stats.mem_usage.toFixed(1) + '%';
                
                if (telemetryChart) {
                    const nowLabel = new Date().toLocaleTimeString();
                    telemetryChart.data.labels.push(nowLabel);
                    telemetryChart.data.datasets[0].data.push(data.stats.cpu_usage);
                    telemetryChart.data.datasets[1].data.push(data.stats.mem_usage);
                    if (telemetryChart.data.labels.length > 8) {
                        telemetryChart.data.labels.shift();
                        telemetryChart.data.datasets[0].data.shift();
                        telemetryChart.data.datasets[1].data.shift();
                    }
                    telemetryChart.update();
                }
            } catch (err) {
                console.error(err);
            }
        }

        async function processPayout(withdrawalId, action) {
            if (!confirm(`Verify payment sweep: Finalize payout #${withdrawalId}?`)) return;
            try {
                const res = await fetch('/api/withdrawal/payout', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ withdrawal_id: withdrawalId, action: action })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast(data.message, 'success');
                    loadAdminDashboard();
                }
            } catch (err) {
                showToast('Payout error.', 'error');
            }
        }

        async function toggleUserFlag(discordId, flagged) {
            const reason = flagged ? prompt('Specify flag reason:') : '';
            if (flagged && reason === null) return;
            try {
                const res = await fetch('/api/withdrawal/payout', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ action: flagged ? 'flag' : 'unflag', discord_id: discordId, reason })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    showToast(data.message, 'success');
                    loadAdminDashboard();
                }
            } catch (err) {
                showToast('Flag error.', 'error');
            }
        }

        function switchSubAuditor(subTab) {
            document.querySelectorAll('.sub-tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.auditor-sub-view').forEach(view => view.style.display = 'none');
            document.getElementById('sub-tab-' + subTab).classList.add('active');
            document.getElementById('auditor-sub-' + subTab).style.display = 'block';
        }

        async function runUserAudit() {
            const val = document.getElementById('txt-bulk-users').value.trim();
            if (!val) return;
            const names = val.split('\\n').map(n=>n.trim()).filter(n=>n);
            const tbl = document.getElementById('tbl-audit-users');
            tbl.innerHTML = `<tr><td colspan="5" style="text-align:center">Scanning profiles...</td></tr>`;
            try {
                const res = await fetch('/api/check/users', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ usernames: names }) });
                const data = await res.json();
                if (res.ok && data.success) {
                    tbl.innerHTML = data.results.map(r => `
                        <tr>
                            <td><strong>u/${r.username}</strong></td>
                            <td><span class="badge ${r.status==='active'?'completed':'flagged'}">${r.status.toUpperCase()}</span></td>
                            <td>${r.karma !== undefined ? r.karma + ' karma' : '-'}</td>
                            <td>${r.age_days !== undefined ? r.age_days + ' days' : '-'}</td>
                            <td>${r.linked_discord ? `<span class="badge blue">Linked: ${r.linked_username}</span>` : 'None'}</td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                tbl.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--color-crimson)">Auditor offline.</td></tr>`;
            }
        }

        async function runPostAudit() {
            const val = document.getElementById('txt-bulk-posts').value.trim();
            if (!val) return;
            const urls = val.split('\\n').map(u=>u.trim()).filter(u=>u);
            const tbl = document.getElementById('tbl-audit-posts');
            tbl.innerHTML = `<tr><td colspan="5" style="text-align:center">Scanning posts...</td></tr>`;
            try {
                const res = await fetch('/api/check/posts', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ urls }) });
                const data = await res.json();
                if (res.ok && data.success) {
                    tbl.innerHTML = data.results.map(r => `
                        <tr>
                            <td><a href="${r.url}" target="_blank" style="color:var(--color-purple-hover)">${r.url.slice(0,30)}...</a></td>
                            <td>${r.author || '-'}</td>
                            <td>r/${r.subreddit || '-'}</td>
                            <td>${r.title || '-'}</td>
                            <td><span class="badge ${r.liveness==='live'?'completed':'flagged'}">${r.liveness.toUpperCase()}</span></td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                tbl.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--color-crimson)">Scraper offline.</td></tr>`;
            }
        }

        async function runCommentAudit() {
            const val = document.getElementById('txt-bulk-comments').value.trim();
            if (!val) return;
            const urls = val.split('\\n').map(u=>u.trim()).filter(u=>u);
            const tbl = document.getElementById('tbl-audit-comments');
            tbl.innerHTML = `<tr><td colspan="5" style="text-align:center">Scanning comments...</td></tr>`;
            try {
                const res = await fetch('/api/check/comments', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ urls }) });
                const data = await res.json();
                if (res.ok && data.success) {
                    tbl.innerHTML = data.results.map(r => `
                        <tr>
                            <td><a href="${r.url}" target="_blank" style="color:var(--color-purple-hover)">${r.url.slice(0,30)}...</a></td>
                            <td>u/${r.author || '-'}</td>
                            <td>r/${r.subreddit || '-'}</td>
                            <td style="font-style:italic">"${r.body_snippet || '-'}"</td>
                            <td><span class="badge ${r.liveness==='live'?'completed':'flagged'}">${r.liveness.toUpperCase()}</span></td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                tbl.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--color-crimson)">Scraper offline.</td></tr>`;
            }
        }

        checkSession();
        setInterval(() => { if (userSession) loadAdminDashboard(); }, 15000);
    </script>
</body>
</html>
"""

# ─── ROUTE HANDLERS ───────────────────────────────────────────────────────────

async def handle_login_page(request: web.Request) -> web.Response:
    """Serve the sleek earner access gateway selector."""
    cookie = request.cookies.get("session_token")
    if cookie and verify_session(cookie):
        return web.HTTPFound("/")
    return web.Response(text=LOGIN_EARNER_HTML, content_type="text/html")

async def handle_client_login_page(request: web.Request) -> web.Response:
    """Serve the advertiser login entry gateway."""
    cookie = request.cookies.get("session_token")
    if cookie and verify_session(cookie):
        return web.HTTPFound("/")
    return web.Response(text=LOGIN_CLIENT_HTML, content_type="text/html")

async def handle_admin_login_page(request: web.Request) -> web.Response:
    """Serve the staff audit portal gateway."""
    cookie = request.cookies.get("session_token")
    if cookie and verify_session(cookie):
        return web.HTTPFound("/")
    return web.Response(text=LOGIN_ADMIN_HTML, content_type="text/html")

async def handle_index(request: web.Request) -> web.Response:
    """Intercept index path and redirect to correct dynamic portal depending on RBAC credentials."""
    cookie = request.cookies.get("session_token")
    if not cookie:
        return web.HTTPFound("/login")
    session = verify_session(cookie)
    if not session:
        return web.HTTPFound("/login")
    
    _, role = session
    if role == "user":
        return web.HTTPFound("/earner")
    elif role == "client":
        return web.HTTPFound("/client")
    else:
        return web.HTTPFound("/admin")

@role_required(["user"], login_redirect="/login")
async def handle_earner_page(request: web.Request) -> web.Response:
    """Serve Earner Dashboard layout page."""
    return web.Response(text=EARNER_DASHBOARD_HTML, content_type="text/html")

@role_required(["client"], login_redirect="/client/login")
async def handle_client_page(request: web.Request) -> web.Response:
    """Serve Advertiser Dashboard layout page."""
    return web.Response(text=CLIENT_DASHBOARD_HTML, content_type="text/html")

@role_required(["staff", "admin", "dev"], login_redirect="/admin/login")
async def handle_admin_page(request: web.Request) -> web.Response:
    """Serve Staff Telemetry Dashboard layout page."""
    return web.Response(text=ADMIN_DASHBOARD_HTML, content_type="text/html")

async def handle_api_login(request: web.Request) -> web.Response:
    """Authenticates credentials against local SQLite or remote PostgreSQL engines based on chosen portal."""
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()
        portal = data.get("portal", "").strip()
        
        if not username or not password or not portal:
            return web.json_response({"success": False, "message": "All authorization coordinates required."}, status=400)
            
        if portal == "earner":
            admin_user = await db.get_admin_user(username)
            if admin_user and admin_user["role"] == "dev" and verify_password(password, admin_user["password_hash"]):
                role = "dev"
                display_name = admin_user.get("display_name") or username
            else:
                user = await db.get_user(username)
                if not user or not user.get("password_hash"):
                    return web.json_response({"success": False, "message": "Profile password not configured. Run `/weblogin` in Discord first."}, status=400)
                if not verify_password(password, user["password_hash"]):
                    return web.json_response({"success": False, "message": "Incorrect earner credentials."}, status=400)
                role = "user"
                display_name = user.get("reddit_username") or f"Earner {username}"
        else:
            user = await db.get_admin_user(username)
            if not user or not user.get("password_hash") or not verify_password(password, user["password_hash"]):
                return web.json_response({"success": False, "message": "Invalid staff or client credentials."}, status=400)
                
            role = user["role"]
            if portal == "client" and role != "client" and role != "dev":
                return web.json_response({"success": False, "message": "Insufficient advertiser access rights."}, status=403)
            if portal == "admin" and role not in ("staff", "admin", "dev"):
                return web.json_response({"success": False, "message": "Insufficient staff access rights."}, status=403)
                
            display_name = user.get("display_name") or username
            
        cookie_val = sign_session(username, role)
        response = web.json_response({
            "success": True,
            "message": "Gateway login successful!",
            "username": username,
            "role": role,
            "display_name": display_name
        })
        response.set_cookie("session_token", cookie_val, path="/", max_age=7 * 86400, httponly=True)
        return response
    except Exception as e:
        logger.error("Authentication api login error: %s", e)
        return web.json_response({"success": False, "message": "Internal credentials compilation error."}, status=500)

async def handle_api_register(request: web.Request) -> web.Response:
    """Validates the 6-digit Discord access token and configures secure permanent password."""
    try:
        data = await request.json()
        discord_id = data.get("discord_id", "").strip()
        token = data.get("token", "").strip()
        password = data.get("password", "").strip()
        
        if not discord_id or not token or not password:
            return web.json_response({"success": False, "message": "All coordinate inputs are required."}, status=400)
            
        user = await db.get_user(discord_id)
        if not user:
            return web.json_response({"success": False, "message": "Discord user not registered in bounty platform. Join server first."}, status=400)
            
        if not user.get("web_login_token") or user["web_login_token"] != token:
            return web.json_response({"success": False, "message": "Invalid or expired 6-digit token. Generate a new token using `/weblogin`."}, status=400)
            
        pwd_hash = hash_password(password)
        await db.set_user_web_password(discord_id, pwd_hash)
        
        return web.json_response({"success": True, "message": "Account successfully secured! You can now log into your Earner Portal."})
    except Exception as e:
        logger.error("Web registration processing error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

async def handle_api_logout(request: web.Request) -> web.Response:
    """Clear signed session cookie."""
    response = web.json_response({"success": True, "message": "Session finalized."})
    response.del_cookie("session_token")
    return response

async def handle_api_session(request: web.Request) -> web.Response:
    """Fetch active session state profile and role indicators."""
    cookie = request.cookies.get("session_token")
    if not cookie:
        return web.json_response({"logged_in": False})
    session = verify_session(cookie)
    if not session:
        return web.json_response({"logged_in": False})
    
    username, role = session
    if role == "user":
        user = await db.get_user(username)
        display_name = user.get("reddit_username") or f"Earner {username}"
    else:
        user = await db.get_admin_user(username)
        display_name = user.get("display_name") if user else username
        
    return web.json_response({
        "logged_in": True,
        "username": username,
        "role": role,
        "display_name": display_name
    })

# ─── EARNER APIS ──────────────────────────────────────────────────────────────

@role_required(["user"])
async def handle_earner_dashboard(request: web.Request) -> web.Response:
    """Compile custom balance, open bounties, active claim details, and withdrawals ledgers for logged earner."""
    try:
        discord_id = request["username"]
        user = await db.get_user(discord_id)
        if not user:
            return web.json_response({"success": False, "message": "Profile registry error."}, status=400)
            
        open_tasks = await db.list_open_tasks()
        
        active_claim = await db.get_active_claim(discord_id)
        if active_claim:
            sub = await db.fetchrow("SELECT * FROM submissions WHERE claim_id = ?;", active_claim["claim_id"])
            active_claim["submitted"] = bool(sub)
            active_claim["submission_status"] = sub["status"] if sub else None
            
            # format timestamps securely
            if isinstance(active_claim["expires_at"], datetime):
                active_claim["expires_at"] = active_claim["expires_at"].isoformat()
            elif hasattr(active_claim["expires_at"], "isoformat"):
                active_claim["expires_at"] = active_claim["expires_at"].isoformat()

        withdrawals = await db.fetch("SELECT * FROM withdrawals WHERE discord_id = ? ORDER BY created_at DESC LIMIT 10;", discord_id)
        for w in withdrawals:
            if isinstance(w["created_at"], datetime):
                w["created_at"] = w["created_at"].isoformat()

        claims = await db.fetch(
            """
            SELECT c.*, t.reward, t.type as task_type, t.target_url, s.status as sub_status, s.proof_url 
            FROM claims c
            JOIN tasks t ON c.task_id = t.task_id
            LEFT JOIN submissions s ON c.claim_id = s.claim_id
            WHERE c.discord_id = ? AND c.status != 'active'
            ORDER BY c.created_at DESC LIMIT 10;
            """,
            discord_id
        )
        for c in claims:
            if isinstance(c["created_at"], datetime):
                c["created_at"] = c["created_at"].isoformat()
                
        stats = {
            "total_earned": (user.get("balance_available") or 0.0) + (user.get("balance_pending") or 0.0),
            "available": user.get("balance_available") or 0.0,
            "pending": user.get("balance_pending") or 0.0,
            "trust_score": user.get("trust_score") or 100,
            "verified": bool(user.get("verified")),
            "reddit_username": user.get("reddit_username")
        }

        return web.json_response({
            "success": True,
            "stats": stats,
            "tasks": open_tasks,
            "active_claim": active_claim,
            "withdrawals": withdrawals,
            "claims_history": claims
        })
    except Exception as e:
        logger.error("Earner dashboard compilation error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

@role_required(["user"])
async def handle_earner_claim(request: web.Request) -> web.Response:
    """Reserve slot claimed for a target campaign bounty task."""
    try:
        discord_id = request["username"]
        user = await db.get_user(discord_id)
        if user and user.get("is_flagged"):
            return web.json_response({"success": False, "message": f"Slot claim blocked: Account flagged. Reason: {user.get('flag_reason')}"}, status=403)
            
        active = await db.get_active_claim(discord_id)
        if active:
            return web.json_response({"success": False, "message": "You already hold an active claimed bounty. Complete it first."}, status=400)
            
        data = await request.json()
        task_id = data.get("task_id")
        if not task_id:
            return web.json_response({"success": False, "message": "Bounty Task ID required."}, status=400)
            
        task = await db.get_task(task_id)
        if not task:
            return web.json_response({"success": False, "message": "Task not found."}, status=404)
            
        claim_id = await db.claim_task(discord_id, task_id, task["time_limit"])
        if not claim_id:
            return web.json_response({"success": False, "message": "Bounty slots filled or campaign inactive."}, status=400)
            
        return web.json_response({"success": True, "message": "Slot claim successful! Complete work within time-limits.", "claim_id": claim_id})
    except Exception as e:
        logger.error("Claim bounty slot error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

@role_required(["user"])
async def handle_earner_submit(request: web.Request) -> web.Response:
    """Submits proof comment links and validates parameters concurrently."""
    try:
        discord_id = request["username"]
        data = await request.json()
        claim_id = int(data.get("claim_id"))
        proof_url = data.get("proof_url", "").strip()
        screenshot_url = data.get("screenshot_url")
        
        if not proof_url:
            return web.json_response({"success": False, "message": "Reddit comment proof link is required."}, status=400)
            
        sub_id = await db.submit_proof(claim_id, discord_id, proof_url, screenshot_url)
        
        # Trigger parallel auto-validation sweep asynchronously
        try:
            from bot.validation import validate_submission
            asyncio.create_task(validate_submission(request.app["bot"], sub_id))
        except Exception as err:
            logger.error("Auto validation bootstrap error: %s", err)
            
        return web.json_response({"success": True, "message": "Proofs successfully submitted! Verification holds active.", "submission_id": sub_id})
    except Exception as e:
        logger.error("Submission proof trigger error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

@role_required(["user"])
async def handle_earner_withdraw(request: web.Request) -> web.Response:
    """Submit withdrawal sweep request validating balance limits."""
    try:
        discord_id = request["username"]
        data = await request.json()
        amount = float(data.get("amount", 0))
        method = data.get("method", "").strip()
        info = data.get("info", "").strip()
        
        if amount <= 0 or not method or not info:
            return web.json_response({"success": False, "message": "Invalid payout parameters."}, status=400)
            
        w_id = await db.request_withdrawal(discord_id, amount, method, info)
        if not w_id:
            return web.json_response({"success": False, "message": "Insufficient available credits balance to initiate sweep."}, status=400)
            
        return web.json_response({"success": True, "message": f"Sweep payout request #{w_id} successfully submitted!", "withdrawal_id": w_id})
    except Exception as e:
        logger.error("Sweep withdrawal api error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

@role_required(["user"])
async def handle_earner_profile(request: web.Request) -> web.Response:
    """Save wallet coordinates coordinates directly to user profile registry."""
    try:
        discord_id = request["username"]
        data = await request.json()
        method = data.get("method")
        value = data.get("value")
        network = data.get("network")
        
        if not method or not value:
            return web.json_response({"success": False, "message": "Invalid details coordinates."}, status=400)
            
        await db.update_user_wallet(discord_id, method, value, network)
        return web.json_response({"success": True, "message": f"Payment coordinates for {method.upper()} successfully saved."})
    except Exception as e:
        logger.error("Save profile credentials error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

# ─── CLIENT APIS ──────────────────────────────────────────────────────────────

@role_required(["client"])
async def handle_client_dashboard(request: web.Request) -> web.Response:
    """Compile client advertiser statistics, campaigns analytics, and funded ledger records."""
    try:
        campaigns = await db.fetch("SELECT * FROM campaigns ORDER BY status ASC;")
        for c in campaigns:
            c_id = c["campaign_id"]
            c_tasks = await db.fetch("SELECT COUNT(*) as count FROM tasks WHERE campaign_id = ?;", c_id)
            c["comment_count"] = c_tasks[0]["count"] if c_tasks else 0
            
            filled_res = await db.fetchrow("SELECT SUM(slots_filled) as filled, SUM(slots_total) as total FROM tasks WHERE campaign_id = ?;", c_id)
            c["slots_filled"] = filled_res["filled"] if filled_res and filled_res["filled"] else 0
            c["slots_total"] = filled_res["total"] if filled_res and filled_res["total"] else 0
              
        spent_res = await db.fetchrow(
            """
            SELECT SUM(t.reward * t.slots_filled) as total 
            FROM tasks t
            JOIN campaigns c ON t.campaign_id = c.campaign_id;
            """
        )
        total_spent = float(spent_res["total"]) if spent_res and spent_res["total"] else 0.0
        
        stats = {
            "total_campaigns": len(campaigns),
            "total_spent": total_spent,
            "wallet_balance": 5000.00 # Seeded campaign credit budget
        }
        
        return web.json_response({
            "success": True,
            "stats": stats,
            "campaigns": campaigns
        })
    except Exception as e:
        logger.error("Client dashboard compilation error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)

# ─── SCREENSHOT FILE PROOF UPLOADS ────────────────────────────────────────────

@role_required(["user"])
async def handle_proof_upload(request: web.Request) -> web.Response:
    """Processes screenshot multipart file upload and returns static serving link URL."""
    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != "file":
            return web.json_response({"success": False, "message": "Invalid file upload fields."}, status=400)
        
        filename = field.filename
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            return web.json_response({"success": False, "message": "Only screenshot formats (.png, .jpg, .webp) allowed."}, status=400)
            
        file_uuid = str(uuid.uuid4())
        save_name = f"{file_uuid}{ext}"
        save_path = os.path.join("data/proofs", save_name)
        
        size = 0
        with open(save_path, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    return web.json_response({"success": False, "message": "File exceeds 5MB bounds."}, status=400)
                f.write(chunk)
                
        url = f"/proofs/{save_name}"
        return web.json_response({"success": True, "url": url})
    except Exception as e:
        logger.error("File upload error: %s", e)
        return web.json_response({"success": False, "message": "Failed to compile uploaded screenshot."}, status=500)

# ─── ADMIN & INFRASTRUCTURE TELEMETRY APIS ────────────────────────────────────

@role_required(["staff", "admin", "dev"])
async def handle_api_data(request: web.Request) -> web.Response:
    """Compiles administrative payout sweep queues, verified earner databases, and dev telemetry."""
    try:
        users = await db.fetch("SELECT * FROM users ORDER BY balance_available DESC;")

        bot: discord.Client = request.app["bot"]
        for u in users:
            d_id = int(u["discord_id"])
            user_obj = bot.get_user(d_id)
            u["discord_username"] = str(user_obj) if user_obj else None

        campaigns = await db.fetch("SELECT * FROM campaigns ORDER BY status ASC;")
        for c in campaigns:
            c_id = c["campaign_id"]
            c_tasks = await db.fetch("SELECT COUNT(*) as count FROM tasks WHERE campaign_id = ?;", c_id)
            c["comment_count"] = c_tasks[0]["count"] if c_tasks else 0

        withdrawals = await db.fetch("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY created_at ASC;")
        for w in withdrawals:
            d_id = int(w["discord_id"])
            user_obj = bot.get_user(d_id)
            w["discord_username"] = str(user_obj) if user_obj else None

        total_campaigns = len(campaigns)
        total_users = len(users)
        pending_withdrawals_volume = sum(float(w["amount"]) for w in withdrawals)

        paid_res = await db.fetchrow("SELECT SUM(amount) as total FROM withdrawals WHERE status = 'completed';")
        completed_withdrawals_volume = float(paid_res["total"]) if paid_res and paid_res["total"] else 0.0

        telemetry = get_system_telemetry()

        stats = {
            "total_campaigns": total_campaigns,
            "total_users": total_users,
            "pending_withdrawals_volume": pending_withdrawals_volume,
            "completed_withdrawals_volume": completed_withdrawals_volume,
            "cpu_usage": telemetry["cpu"],
            "mem_usage": telemetry["memory"],
        }

        return web.json_response(
            {
                "success": True,
                "stats": stats,
                "users": users,
                "campaigns": campaigns,
                "withdrawals": withdrawals,
            }
        )

    except Exception as exc:
        logger.error("Admin dashboard compilation statistics error: %s", exc)
        return web.json_response(
            {"success": False, "message": "Failed to compile DB logs statistics."},
            status=500,
        )

@role_required(["staff", "admin", "dev"])
async def handle_payout_action(request: web.Request) -> web.Response:
    """Moderation payout confirmation sweeps and user flagging controls."""
    try:
        data = await request.json()
        action = data.get("action")
        bot: discord.Client = request.app["bot"]

        if action in ("approve", "reject"):
            w_id = int(data.get("withdrawal_id"))
            w_record = await db.fetchrow("SELECT * FROM withdrawals WHERE withdrawal_id = ?;", w_id)
            if not w_record:
                return web.json_response({"success": False, "message": "Withdrawal request not found."}, status=404)
            
            if w_record["status"] != "pending":
                return web.json_response({"success": False, "message": "Payout already finalized."}, status=400)
            
            discord_id = w_record["discord_id"]
            amount = float(w_record["amount"])
            method = w_record["payment_method"]
            info = w_record["payment_info"]

            guild = bot.guilds[0] if bot.guilds else None
            member = guild.get_member(int(discord_id)) if guild else None

            if action == "approve":
                await db.finalize_withdrawal(w_id, "WebAdmin")
                if member:
                    try:
                        await member.send(
                            f"**Your payout sweep request #{w_id} for {amount:.2f} credits was approved!**\n"
                            f"Credits has been successfully cashed out to: **{method.upper()}** ({info})."
                        )
                    except Exception:
                        pass
                
                if guild:
                    logs_chan = discord.utils.get(guild.text_channels, name="withdrawal-logs")
                    if logs_chan:
                        embed = discord.Embed(
                            title=f"Paid Sweep #{w_id}",
                            description=(
                                f"**User**: {member.mention if member else 'ID: ' + discord_id}\n"
                                f"**Reward**: **{amount:.2f} credits**\n"
                                f"**Coordinates**: {method.upper()} ({info})\n"
                                f"**Moderator**: `WebAdmin Dashboard`"
                            ),
                            color=discord.Color.green(),
                        )
                        await logs_chan.send(embed=embed)

                return web.json_response({"success": True, "message": f"Sweep #{w_id} approved & paid!"})

            elif action == "reject":
                reason = "Moderator rejected payment coordinates."
                await db.execute("UPDATE users SET balance_available = balance_available + ? WHERE discord_id = ?;", amount, discord_id)
                await db.execute("UPDATE withdrawals SET status = 'rejected' WHERE withdrawal_id = ?;", w_id)
                
                if member:
                    try:
                        await member.send(
                            f"**Your payout sweep #{w_id} was rejected.**\n"
                            f"Credits balance has been fully refunded back to available catalog.\n"
                            f"**Reason**: {reason}"
                        )
                    except Exception:
                        pass

                if guild:
                    logs_chan = discord.utils.get(guild.text_channels, name="withdrawal-logs")
                    if logs_chan:
                        embed = discord.Embed(
                            title=f"Rejected Sweep #{w_id}",
                            description=(
                                f"**User**: {member.mention if member else 'ID: ' + discord_id}\n"
                                f"**Amount**: **{amount:.2f} credits**\n"
                                f"**State**: Refunded Balance\n"
                                f"**Reason**: {reason}"
                            ),
                            color=discord.Color.red(),
                        )
                        await logs_chan.send(embed=embed)

                return web.json_response({"success": True, "message": f"Sweep #{w_id} rejected and refunded."})

        elif action in ("flag", "unflag"):
            discord_id = data.get("discord_id")
            reason = data.get("reason", "Violating bounty terms.")
            flagged = action == "flag"

            await db.set_user_flag(discord_id, flagged, reason if flagged else None)

            guild = bot.guilds[0] if bot.guilds else None
            member = guild.get_member(int(discord_id)) if guild else None

            if flagged:
                if member:
                    try:
                        await member.send(
                            f"**Your redditOS earner account has been flagged by moderator.**\n"
                            f"Task claims and payout sweeps have been blocked.\n"
                            f"**Reason**: {reason}"
                        )
                    except Exception:
                        pass
                
                if guild:
                    logs_chan = discord.utils.get(guild.text_channels, name="task-logs")
                    if logs_chan:
                        embed = discord.Embed(
                            title="Account Flagged",
                            description=(
                                f"**User**: {member.mention if member else 'ID: ' + discord_id}\n"
                                f"**Reason**: {reason}"
                            ),
                            color=discord.Color.red(),
                        )
                        await logs_chan.send(embed=embed)

                return web.json_response({"success": True, "message": "User flagged."})
            else:
                if member:
                    try:
                        await member.send("**Your account has been restored!** Claims and withdrawals are active.")
                    except Exception:
                        pass
                
                if guild:
                    logs_chan = discord.utils.get(guild.text_channels, name="task-logs")
                    if logs_chan:
                        embed = discord.Embed(
                            title="Account Restored",
                            description=f"**User**: {member.mention if member else 'ID: ' + discord_id}",
                            color=discord.Color.green(),
                        )
                        await logs_chan.send(embed=embed)

                return web.json_response({"success": True, "message": "Flags cleared."})

        return web.json_response({"success": False, "message": "Invalid moderation directive."}, status=400)
    except Exception as exc:
        logger.error("Payout action error: %s", exc)
        return web.json_response({"success": False, "message": "Failed processing moderator hook."}, status=500)

# ─── BULK AUDITOR SCRAPER ENDPOINTS ───────────────────────────────────────────

async def handle_campaign_import(request: web.Request) -> web.Response:
    """Asynchronously parses and deploys structural campaigns directly from seeder textbox pastes."""
    try:
        data = await request.json()
        content = data.get("content", "").strip()
        if not content:
            return web.json_response({"success": False, "message": "Import block content is empty."}, status=400)

        bot: commands.Bot = request.app["bot"]
        campaign_cog = bot.get_cog("CampaignCommands")
        if not campaign_cog:
            return web.json_response({"success": False, "message": "Campaign deployment cog is active but currently unavailable."}, status=503)

        from scratch.test_campaign_parser import parse_campaign_text
        campaign_data = parse_campaign_text(content)
        if not campaign_data or not campaign_data.get("comments"):
            return web.json_response({"success": False, "message": "Structured parser failed. Ensure template follows required seeding hierarchy."}, status=400)

        guild = bot.guilds[0] if bot.guilds else None
        if not guild:
            return web.json_response({"success": False, "message": "Discord bot is online but has not connected to any servers."}, status=503)

        from commands.campaign import deploy_campaign_from_parsed
        await deploy_campaign_from_parsed(bot, guild, campaign_data)

        return web.json_response(
            {"success": True, "message": f"Successfully parsed and deployed campaign: {campaign_data.get('subreddit')}"}
        )
    except Exception as exc:
        logger.error("Error deploying campaign paste from dashboard: %s", exc)
        return web.json_response(
            {"success": False, "message": f"Import error: {str(exc)}"},
            status=500,
        )

async def handle_check_users(request: web.Request) -> web.Response:
    """Bulk check usernames for existence, karma, age, and linked profiles."""
    try:
        data = await request.json()
        usernames = data.get("usernames", [])
        if not usernames:
            return web.json_response({"success": False, "message": "No usernames provided."}, status=400)

        router = get_router()
        results = []

        async def check_user(username: str) -> dict[str, Any]:
            username = username.strip().lstrip("u/").lstrip("/u/")
            if not username:
                return {"username": "", "status": "invalid"}
            try:
                about = await router.get_user_about(username)
                if not about:
                    return {"username": username, "status": "not_found"}
                
                from models.user import RedditUser
                user_obj = RedditUser.from_json(about)
                linked = await db.get_user_by_reddit(username)
                
                return {
                    "username": username,
                    "status": "active",
                    "karma": user_obj.total_karma,
                    "age_days": user_obj.account_age_days,
                    "created_utc": user_obj.created_utc,
                    "linked_discord": linked["discord_id"] if linked else None
                }
            except Exception as e:
                logger.error("Error scraping user about in web API: %s", e)
                return {"username": username, "status": "error", "message": str(e)}

        tasks = [check_user(name) for name in usernames[:15]]
        results = await asyncio.gather(*tasks)

        bot = request.app["bot"]
        for r in results:
            if r.get("linked_discord"):
                user_obj = bot.get_user(int(r["linked_discord"]))
                r["linked_username"] = str(user_obj) if user_obj else f"ID: {r['linked_discord']}"

        return web.json_response({"success": True, "results": results})
    except Exception as exc:
        logger.error("Error bulk checking users: %s", exc)
        return web.json_response({"success": False, "message": str(exc)}, status=500)

async def handle_check_posts(request: web.Request) -> web.Response:
    """Bulk verify liveness and metadata for post URLs."""
    try:
        data = await request.json()
        urls = data.get("urls", [])
        if not urls:
            return web.json_response({"success": False, "message": "No URLs provided."}, status=400)

        router = get_router()
        results = []

        async def check_post(url: str) -> dict[str, Any]:
            url = url.strip()
            if not url:
                return {"url": "", "status": "invalid"}
            try:
                parts = _extract_post_parts(url)
                if not parts:
                    return {"url": url, "status": "invalid_url", "message": "Could not parse Reddit post ID."}
                
                post_data, _ = await router.get_post_and_comments(url)
                if not post_data:
                    return {"url": url, "status": "not_found"}
                
                author = post_data.get("author", "")
                selftext = post_data.get("selftext", "")
                title = post_data.get("title", "")
                subreddit = post_data.get("subreddit", "")
                
                liveness = "live"
                if author == "[deleted]" or selftext in ("[deleted]", "[removed]"):
                    liveness = "removed"
                
                return {
                    "url": url,
                    "status": "success",
                    "liveness": liveness,
                    "author": author,
                    "subreddit": subreddit,
                    "title": title
                }
            except Exception as e:
                logger.error("Error scraping post liveness in web API: %s", e)
                return {"url": url, "status": "error", "message": str(e)}

        tasks = [check_post(u) for u in urls[:10]]
        results = await asyncio.gather(*tasks)
        return web.json_response({"success": True, "results": results})
    except Exception as exc:
        logger.error("Error bulk checking posts: %s", exc)
        return web.json_response({"success": False, "message": str(exc)}, status=500)

async def handle_check_comments(request: web.Request) -> web.Response:
    """Bulk verify liveness and comment author strings."""
    try:
        data = await request.json()
        urls = data.get("urls", [])
        if not urls:
            return web.json_response({"success": False, "message": "No URLs provided."}, status=400)

        router = get_router()
        results = []

        async def check_comment(url: str) -> dict[str, Any]:
            url = url.strip()
            if not url:
                return {"url": "", "status": "invalid"}
            try:
                c_id = _extract_comment_id(url)
                if not c_id:
                    return {"url": url, "status": "invalid_url", "message": "Could not parse Reddit comment ID."}
                
                context = await router.get_comment_context(url)
                if not context or not context[0]:
                    return {"url": url, "status": "not_found"}
                
                comment = context[0]
                author = comment.get("author", "")
                body = comment.get("body", "")
                subreddit = comment.get("subreddit", "")
                
                liveness = "live"
                if author == "[deleted]" or body in ("[deleted]", "[removed]"):
                    liveness = "removed"
                
                return {
                    "url": url,
                    "status": "success",
                    "liveness": liveness,
                    "author": author,
                    "subreddit": subreddit,
                    "body_snippet": body[:120] + "..." if len(body) > 120 else body
                }
            except Exception as e:
                logger.error("Error scraping comment liveness in web API: %s", e)
                return {"url": url, "status": "error", "message": str(e)}

        tasks = [check_comment(u) for u in urls[:10]]
        results = await asyncio.gather(*tasks)
        return web.json_response({"success": True, "results": results})
    except Exception as exc:
        logger.error("Error bulk checking comments: %s", exc)
        return web.json_response({"success": False, "message": str(exc)}, status=500)


# ─── EXTERNAL API INTEGRATIONS ────────────────────────────────────────────────

async def handle_external_check_user(request: web.Request) -> web.Response:
    """Check a single Reddit username for status, karma, age, and link status."""
    try:
        username = request.match_info.get("username", "").strip().lstrip("u/").lstrip("/u/")
        if not username:
            return web.json_response({"success": False, "message": "Username is required."}, status=400)
            
        router = get_router()
        about = await router.get_user_about(username)
        if not about:
            return web.json_response({"success": False, "message": "Reddit user not found."}, status=404)
            
        from models.user import RedditUser
        user_obj = RedditUser.from_json(about)
        linked = await db.get_user_by_reddit(username)
        
        return web.json_response({
            "success": True,
            "data": {
                "username": username,
                "status": "active",
                "karma": user_obj.total_karma,
                "age_days": user_obj.account_age_days,
                "created_utc": user_obj.created_utc,
                "linked_discord": linked["discord_id"] if linked else None
            }
        })
    except Exception as e:
        logger.error("External check user error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)


async def handle_external_check_post(request: web.Request) -> web.Response:
    """Verify liveness and fetch metadata of a single Reddit post URL."""
    try:
        url = request.query.get("url", "").strip()
        if not url:
            return web.json_response({"success": False, "message": "url query parameter is required."}, status=400)
            
        parts = _extract_post_parts(url)
        if not parts:
            return web.json_response({"success": False, "message": "Could not parse Reddit post ID from URL."}, status=400)
            
        router = get_router()
        post_data, _ = await router.get_post_and_comments(url)
        if not post_data:
            return web.json_response({"success": False, "message": "Post not found or unreachable."}, status=404)
            
        author = post_data.get("author", "")
        selftext = post_data.get("selftext", "")
        liveness = "live"
        if author == "[deleted]" or selftext in ("[deleted]", "[removed]"):
            liveness = "removed"
            
        return web.json_response({
            "success": True,
            "data": {
                "url": url,
                "liveness": liveness,
                "author": author,
                "subreddit": post_data.get("subreddit", ""),
                "title": post_data.get("title", ""),
                "upvotes": post_data.get("ups", 0) if "ups" in post_data else post_data.get("score", 0),
                "created_utc": post_data.get("created_utc", 0)
            }
        })
    except Exception as e:
        logger.error("External check post error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)


async def handle_external_check_comment(request: web.Request) -> web.Response:
    """Verify liveness and fetch metadata of a single Reddit comment URL."""
    try:
        url = request.query.get("url", "").strip()
        if not url:
            return web.json_response({"success": False, "message": "url query parameter is required."}, status=400)
            
        c_id = _extract_comment_id(url)
        if not c_id:
            return web.json_response({"success": False, "message": "Could not parse Reddit comment ID from URL."}, status=400)
            
        router = get_router()
        context = await router.get_comment_context(url)
        if not context or not context[0]:
            return web.json_response({"success": False, "message": "Comment not found or unreachable."}, status=404)
            
        comment = context[0]
        author = comment.get("author", "")
        body = comment.get("body", "")
        liveness = "live"
        if author == "[deleted]" or body in ("[deleted]", "[removed]"):
            liveness = "removed"
            
        return web.json_response({
            "success": True,
            "data": {
                "url": url,
                "liveness": liveness,
                "author": author,
                "subreddit": comment.get("subreddit", ""),
                "body_snippet": body[:120] + ("..." if len(body) > 120 else ""),
                "upvotes": comment.get("score"),
                "createdAt": comment.get("created_utc")
            }
        })
    except Exception as e:
        logger.error("External check comment error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)


async def handle_external_verify(request: web.Request) -> web.Response:
    """Verify criteria and link a Reddit account to a Discord ID."""
    try:
        data = await request.json()
        discord_id = str(data.get("discord_id", "")).strip()
        reddit_username = data.get("reddit_username", "").strip().lstrip("u/").lstrip("/u/")
        
        if not discord_id or not reddit_username:
            return web.json_response({"success": False, "message": "Both discord_id and reddit_username are required."}, status=400)
            
        # 1. Database validation checks
        user = await db.get_user(discord_id)
        if user and user.get("verified"):
            return web.json_response({"success": False, "message": "User is already verified."}, status=400)
            
        if user and user.get("is_flagged"):
            return web.json_response({"success": False, "message": f"Verification blocked: user is flagged. Reason: {user.get('flag_reason')}"}, status=400)
            
        existing = await db.get_user_by_reddit(reddit_username)
        if existing and existing["discord_id"] != discord_id:
            return web.json_response({"success": False, "message": f"Reddit account u/{reddit_username} is already linked to another Discord user."}, status=400)
            
        # 2. Fetch Reddit metrics
        router = get_router()
        about = await router.get_user_about(reddit_username)
        if not about:
            return web.json_response({"success": False, "message": "Reddit profile is unreachable or does not exist."}, status=404)
            
        from models.user import RedditUser
        reddit_user = RedditUser.from_json(about)
        
        # Min Requirements
        if reddit_user.total_karma < 100:
            return web.json_response({
                "success": False, 
                "message": f"Verification failed: u/{reddit_username} has {reddit_user.total_karma} karma (min 100 required)."
            }, status=400)
            
        if reddit_user.account_age_days < 30:
            return web.json_response({
                "success": False, 
                "message": f"Verification failed: u/{reddit_username} is {reddit_user.account_age_days} days old (min 30 required)."
            }, status=400)
            
        # 3. Save link to DB
        await db.update_user_reddit(discord_id, reddit_username, verified=True)
        
        # Check for referrals
        ref_row = await db.fetchrow(
            "SELECT * FROM referrals WHERE referee_id = ? AND credited = 0;", discord_id
        )
        if ref_row:
            referrer_id = ref_row["referrer_id"]
            await db.execute(
                "UPDATE users SET balance_available = balance_available + 50.0 WHERE discord_id = ?;",
                referrer_id
            )
            await db.execute(
                "UPDATE referrals SET credited = 1 WHERE referee_id = ?;", discord_id
            )
            
        # Try to grant Discord Verified Role & Workspace Channel
        try:
            bot = request.app["bot"]
            if bot.guilds:
                guild = bot.guilds[0]
                member = guild.get_member(int(discord_id))
                if member:
                    from commands.verify import ROLE_VERIFIED
                    role = discord.utils.get(guild.roles, name=ROLE_VERIFIED)
                    if not role:
                        role = await guild.create_role(name=ROLE_VERIFIED, color=discord.Color.blue())
                    await member.add_roles(role)
                    
                    from bot.workspace import get_or_create_workspace_channel
                    await get_or_create_workspace_channel(guild, member)
                    
                    if ref_row:
                        referrer_member = guild.get_member(int(referrer_id))
                        if referrer_member:
                            await referrer_member.send(
                                f"**Referral Credited!** Your referee (ID: {discord_id}) "
                                f"has verified! **+50.0** reward has been credited to your available balance."
                            )
        except Exception as role_err:
            logger.warning("Could not auto-assign Discord verified role/workspace: %s", role_err)
            
        return web.json_response({
            "success": True,
            "message": "Account verification and linking successful.",
            "data": {
                "discord_id": discord_id,
                "reddit_username": reddit_username,
                "karma": reddit_user.total_karma,
                "age_days": reddit_user.account_age_days
            }
        })
    except Exception as e:
        logger.error("External verification error: %s", e)
        return web.json_response({"success": False, "message": str(e)}, status=500)


# ─── WEB SERVER BOOTSTRAPPING & RUNNER ────────────────────────────────────────

async def start_web_server(bot: commands.Bot) -> None:
    """Initialize and start the dynamic Multi-Portal Web server concurrently alongside the Discord bot loop."""
    app = web.Application()
    app["bot"] = bot

    # Static screenshots delivery route
    app.router.add_static("/proofs", "data/proofs")

    # Routing Configuration
    app.router.add_get("/", handle_index)
    app.router.add_get("/login", handle_login_page)
    app.router.add_get("/client/login", handle_client_login_page)
    app.router.add_get("/admin/login", handle_admin_login_page)

    app.router.add_post("/api/login", handle_api_login)
    app.router.add_post("/api/register", handle_api_register)
    app.router.add_post("/api/logout", handle_api_logout)
    app.router.add_get("/api/user/session", handle_api_session)

    # 3 Separate Dashboards endpoints
    app.router.add_get("/earner", handle_earner_page)
    app.router.add_get("/client", handle_client_page)
    app.router.add_get("/admin", handle_admin_page)

    # Earner Portal Endpoints
    app.router.add_get("/api/earner/dashboard", handle_earner_dashboard)
    app.router.add_post("/api/earner/claim", handle_earner_claim)
    app.router.add_post("/api/earner/submit", handle_earner_submit)
    app.router.add_post("/api/earner/withdraw", handle_earner_withdraw)
    app.router.add_post("/api/earner/profile", handle_earner_profile)
    app.router.add_post("/api/proof/upload", handle_proof_upload)

    # Client Portal Endpoints
    app.router.add_get("/api/client/dashboard", handle_client_dashboard)

    # Admin Portal Endpoints & Scrapers Hooks
    app.router.add_get("/api/data", handle_api_data)
    app.router.add_post("/api/withdrawal/payout", handle_payout_action)
    app.router.add_post("/api/campaign/import", handle_campaign_import)
    app.router.add_post("/api/check/users", handle_check_users)
    app.router.add_post("/api/check/posts", handle_check_posts)
    app.router.add_post("/api/check/comments", handle_check_comments)

    # External Integration API Endpoints
    app.router.add_get("/api/external/check/user/{username}", handle_external_check_user)
    app.router.add_get("/api/external/check/post", handle_external_check_post)
    app.router.add_get("/api/external/check/comment", handle_external_check_comment)
    app.router.add_post("/api/external/verify", handle_external_verify)

    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 5000))
    site = web.TCPSite(runner, "0.0.0.0", port, reuse_address=True, reuse_port=True)
    await site.start()
    
    logger.info("=" * 60)
    logger.info(f"redditOS Ultra-Premium Multi-Portal live on port {port}")
    logger.info("=" * 60)

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Web Portal application shutdown trigger received.")
    finally:
        await runner.cleanup()
