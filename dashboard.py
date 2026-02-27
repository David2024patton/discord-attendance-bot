"""
Admin Dashboard for Discord Attendance Bot
aiohttp web server running on port 8080 alongside the bot.
"""

import os
import json
import asyncio
import hashlib
import secrets
import time
from collections import deque
import battle_engine
from datetime import datetime
from aiohttp import web

# ── Shared state (injected by bot.py) ────────────────────────────
bot_ref = None           # reference to the discord bot
log_buffer = deque(maxlen=500)   # ring buffer for log lines
_state_getters = {}      # functions to get bot state

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Wildcats@4113")
SESSION_TOKENS = {}      # token -> expiry
TOKEN_TTL = 86400        # 24h

# ── User Name Cache (prevents sequential Discord API hangs) ──────
_name_cache = {}       # uid -> (name, timestamp)
_NAME_CACHE_TTL = 300  # 5 minutes

async def _resolve_name(uid):
    """Resolve a user ID to display name, with 5-minute cache."""
    now = time.time()
    cached = _name_cache.get(uid)
    if cached and (now - cached[1]) < _NAME_CACHE_TTL:
        return cached[0]
    if bot_ref:
        try:
            u = await bot_ref.fetch_user(int(uid))
            name = u.display_name
            _name_cache[uid] = (name, now)
            return name
        except:
            pass
    return str(uid)

async def _resolve_names(uids):
    """Batch resolve multiple user IDs. Uses cache for speed."""
    results = {}
    to_fetch = []
    now = time.time()
    for uid in uids:
        cached = _name_cache.get(uid)
        if cached and (now - cached[1]) < _NAME_CACHE_TTL:
            results[uid] = cached[0]
        else:
            to_fetch.append(uid)
    # Fetch uncached in parallel-ish
    for uid in to_fetch:
        if bot_ref:
            try:
                u = await bot_ref.fetch_user(int(uid))
                name = u.display_name
                _name_cache[uid] = (name, now)
                results[uid] = name
            except:
                results[uid] = str(uid)
        else:
            results[uid] = str(uid)
    return results

def register_state_getters(getters: dict):
    """Called by bot.py to register functions that return current bot state."""
    global _state_getters
    _state_getters = getters

def add_log(msg: str):
    """Add a log entry to the ring buffer."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_buffer.append(f"[{ts}] {msg}")

# SSE subscribers
_sse_queues = []

async def push_log(msg: str):
    """Push a log line to all SSE subscribers."""
    add_log(msg)
    for q in list(_sse_queues):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

# ── Auth helpers ─────────────────────────────────────────────────
def _hash(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def _check_auth(request):
    token = request.cookies.get("session")
    if not token:
        return False
    exp = SESSION_TOKENS.get(token)
    if not exp or time.time() > exp:
        SESSION_TOKENS.pop(token, None)
        return False
    return True

# ── CSS ──────────────────────────────────────────────────────────
CSS = """
:root {
    --bg: #1a1b1e; --bg2: #25262b; --bg3: #2c2e33;
    --accent: #5865f2; --accent-hover: #4752c4;
    --green: #43b581; --red: #f04747; --orange: #faa61a;
    --text: #dcddde; --text-dim: #96989d; --text-bright: #ffffff;
    --border: #3a3b3f; --radius: 8px;
    --sidebar-w: 240px; --sidebar-collapsed: 60px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif; min-height: 100vh; display: flex; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Sidebar */
.sidebar { width: var(--sidebar-w); min-height: 100vh; background: var(--bg2); border-right: 1px solid var(--border); display: flex; flex-direction: column; position: fixed; top: 0; left: 0; z-index: 200; transition: width 0.25s ease; overflow: hidden; box-shadow: 2px 0 10px rgba(0,0,0,0.2); }
.sidebar.collapsed { width: var(--sidebar-collapsed); }
.sidebar-header { padding: 24px 16px; display: flex; align-items: center; gap: 12px; border-bottom: 1px solid var(--border); min-height: 72px; }
.sidebar-brand { font-size: 18px; font-weight: 800; color: var(--text-bright); white-space: nowrap; overflow: hidden; letter-spacing: -0.3px; }
.sidebar-brand span { color: var(--accent); }
.sidebar.collapsed .sidebar-brand { display: none; }
.toggle-btn { background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 18px; padding: 6px; border-radius: 6px; transition: all 0.2s; flex-shrink: 0; display: flex; align-items: center; justify-content: center; opacity: 0.7; }
.toggle-btn:hover { background: rgba(255,255,255,0.05); color: var(--text-bright); opacity: 1; }
.sidebar-nav { flex: 1; padding: 16px 12px; display: flex; flex-direction: column; gap: 4px; }
.sidebar-nav-title { font-size: 11px; text-transform: uppercase; color: var(--text-dim); font-weight: 700; letter-spacing: 0.8px; padding: 8px 12px 4px; opacity: 0.6; }
.sidebar.collapsed .sidebar-nav-title { display: none; }
.sidebar-nav a { display: flex; align-items: center; gap: 14px; padding: 12px 14px; border-radius: 8px; color: var(--text-dim); font-weight: 500; font-size: 14px; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1); white-space: nowrap; overflow: hidden; text-decoration: none; border: 1px solid transparent; }
.sidebar-nav a:hover { background: rgba(255,255,255,0.03); color: var(--text-bright); transform: translateX(2px); }
.sidebar-nav a.active { background: rgba(88,101,242,0.1); color: var(--accent); border: 1px solid rgba(88,101,242,0.2); box-shadow: 0 4px 12px rgba(0,0,0,0.1); font-weight: 600; }
.sidebar-nav a .icon { font-size: 16px; min-width: 24px; text-align: center; flex-shrink: 0; opacity: 0.8; transition: opacity 0.2s; }
.sidebar-nav a:hover .icon, .sidebar-nav a.active .icon { opacity: 1; }
.sidebar-nav a .label { overflow: hidden; transition: opacity 0.2s; }
.sidebar.collapsed .sidebar-nav a .label { display: none; }
.sidebar-footer { padding: 16px 12px; border-top: 1px solid var(--border); background: rgba(0,0,0,0.1); }
.sidebar-footer a { display: flex; align-items: center; gap: 14px; padding: 10px 14px; border-radius: 8px; color: var(--text-dim); font-size: 13px; font-weight: 500; transition: all 0.2s; text-decoration: none; }
.sidebar-footer a:hover { background: rgba(240,71,71,0.1); color: var(--red); }
.sidebar-footer a .icon { font-size: 16px; min-width: 24px; text-align: center; flex-shrink: 0; }
.sidebar.collapsed .sidebar-footer a .label { display: none; }

/* Main content area */
.main { margin-left: var(--sidebar-w); flex: 1; min-height: 100vh; transition: margin-left 0.25s ease; }
.main.shifted { margin-left: var(--sidebar-collapsed); }

/* Layout */
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
.page-title { font-size: 22px; font-weight: 700; color: var(--text-bright); margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.grid { display: grid; gap: 16px; }
.grid-3 { grid-template-columns: repeat(3, 1fr); }
.grid-2 { grid-template-columns: repeat(2, 1fr); }

/* Cards */
.card { background: var(--bg2); border: 1px solid var(--border); border-radius: 10px; padding: 20px; transition: transform 0.2s, box-shadow 0.2s; }
.card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
.card-header { font-size: 13px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; letter-spacing: 0.5px; margin-bottom: 12px; }
.card-value { font-size: 32px; font-weight: 700; color: var(--text-bright); }
.card-value.green { color: var(--green); }
.card-value.red { color: var(--red); }
.card-value.orange { color: var(--orange); }

/* Clickable dino card */
.dino-card { cursor: pointer; border-top: 3px solid var(--border); position: relative; transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1); }
.dino-card:hover { border-color: var(--accent); box-shadow: 0 8px 30px rgba(88,101,242,0.15); transform: translateY(-4px); }
.dino-card .card-actions { position: absolute; top: 8px; right: 8px; display: flex; gap: 4px; opacity: 0; transition: opacity 0.2s; }
.dino-card:hover .card-actions { opacity: 1; }

/* Tables */
table { width: 100%; border-collapse: collapse; }
th { text-align: left; padding: 10px 12px; font-size: 12px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; letter-spacing: 0.5px; border-bottom: 2px solid var(--border); }
td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 14px; }
tr:hover td { background: var(--bg3); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
.badge-green { background: rgba(67,181,129,0.2); color: var(--green); }
.badge-red { background: rgba(240,71,71,0.2); color: var(--red); }
.badge-orange { background: rgba(250,166,26,0.2); color: var(--orange); }

/* Buttons */
.btn { padding: 6px 14px; border-radius: var(--radius); border: none; cursor: pointer; font-size: 13px; font-weight: 600; transition: all 0.15s; }
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-danger { background: var(--red); color: white; }
.btn-danger:hover { background: #d63031; }
.btn-sm { padding: 4px 10px; font-size: 12px; }

/* Logs */
.log-container { background: #0d1117; border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; font-family: 'Consolas', 'Fira Code', monospace; font-size: 13px; height: 600px; overflow-y: auto; line-height: 1.6; }
.log-line { color: var(--text-dim); white-space: pre-wrap; word-break: break-all; }
.log-line .ts { color: var(--accent); }
.log-line.error { color: var(--red); }
.log-line.success { color: var(--green); }

/* Login */
.login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; width: 100%; }
.login-box { background: var(--bg2); border: 1px solid var(--border); border-radius: 12px; padding: 40px; width: 360px; text-align: center; }
.login-box h2 { margin-bottom: 24px; color: var(--text-bright); }
.login-box input { width: 100%; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); font-size: 14px; margin-bottom: 16px; outline: none; }
.login-box input:focus { border-color: var(--accent); }
.login-box .btn { width: 100%; padding: 10px; font-size: 15px; }
.login-error { color: var(--red); font-size: 13px; margin-bottom: 12px; display: none; }

/* Status dot */
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.dot-green { background: var(--green); }
.dot-red { background: var(--red); }
.dot-orange { background: var(--orange); }

/* Settings */
.setting-row { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--border); }
.setting-row:last-child { border-bottom: none; }
.setting-label { font-weight: 500; }
.setting-desc { font-size: 12px; color: var(--text-dim); }
.setting-input { background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius); color: var(--text); padding: 6px 10px; width: 80px; text-align: center; font-size: 14px; }

/* List */
.user-list { list-style: none; }
.user-list li { padding: 8px 0; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
.user-list li:last-child { border-bottom: none; }

/* Toast */
.toast { position: fixed; bottom: 24px; right: 24px; background: var(--green); color: white; padding: 12px 20px; border-radius: var(--radius); font-size: 14px; font-weight: 600; transform: translateY(100px); opacity: 0; transition: all 0.3s; z-index: 999; }
.toast.show { transform: translateY(0); opacity: 1; }

/* Role chips */
.role-chip { display:inline-flex; align-items:center; gap:4px; padding:4px 10px; border-radius:14px; font-size:12px; font-weight:600; cursor:pointer; border:2px solid transparent; transition:all 0.15s; margin:3px; user-select:none; }
.role-chip input { display:none; }
.role-chip:hover { filter:brightness(1.2); }
.role-chip.selected { border-color:var(--text-bright); }
.role-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.role-section-title { font-size:12px; color:var(--text-dim); text-transform:uppercase; letter-spacing:0.5px; font-weight:600; margin-bottom:6px; margin-top:10px; }
.role-section-title:first-child { margin-top:0; }
.role-list { max-height:200px; overflow-y:auto; padding:4px 0; }

/* Tooltips */
.help-tip { position: relative; display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 50%; background: var(--border); color: var(--text-dim); font-size: 11px; cursor: help; margin-left: 8px; font-weight: bold; vertical-align: middle; user-select: none; }
.help-tip:hover { background: var(--accent); color: white; }
.help-content { visibility: hidden; opacity: 0; position: absolute; bottom: 125%; left: 50%; transform: translateX(-50%); background: var(--bg3); color: var(--text-bright); text-align: center; padding: 6px 12px; border-radius: 6px; font-size: 12px; width: max-content; max-width: 240px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 1000; transition: 0.2s; pointer-events: none; border: 1px solid var(--border); font-weight: normal; line-height: 1.4; white-space: normal; }
.help-tip:hover .help-content { visibility: visible; opacity: 1; bottom: 135%; }
.help-content::after { content: ""; position: absolute; top: 100%; left: 50%; transform: translateX(-50%); border-width: 5px; border-style: solid; border-color: var(--bg3) transparent transparent transparent; }

/* Mobile hamburger - hidden on desktop */
.mobile-hamburger { display:none; }

/* Mobile overlay backdrop */
.sidebar-backdrop { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:199; }
.sidebar-backdrop.active { display:block; }

@media (max-width: 768px) {
    /* Sidebar: off-screen drawer */
    .sidebar { width: var(--sidebar-w); transform: translateX(-100%); transition: transform 0.25s ease; }
    .sidebar.collapsed { width: var(--sidebar-w); transform: translateX(-100%); }
    .sidebar.collapsed .sidebar-brand { display: block; }
    .sidebar.collapsed .sidebar-nav a .label { display: inline; }
    .sidebar.collapsed .sidebar-footer a .label { display: inline; }
    .sidebar.mobile-open { transform: translateX(0); }

    /* Main: full width, no margin */
    .main { margin-left: 0 !important; }

    /* Hamburger: fixed top-left when sidebar hidden */
    .mobile-hamburger { display:flex; position:fixed; top:10px; left:10px; z-index:201; background:var(--bg2); border:1px solid var(--border); border-radius:var(--radius); width:40px; height:40px; align-items:center; justify-content:center; font-size:22px; color:var(--text-dim); cursor:pointer; }
    .mobile-hamburger:hover { background:var(--bg3); color:var(--text-bright); }

    /* Stacked grids */
    .grid-3, .grid-2 { grid-template-columns: 1fr; }
    .container { padding: 12px; padding-top: 56px; }
    .page-title { font-size: 18px; }

    /* Touch-friendly inputs */
    .setting-input { font-size: 16px; padding: 10px 12px; }
    .setting-row { flex-direction: column; align-items: flex-start; gap: 8px; }
    .btn { padding: 10px 16px; font-size: 14px; }
    select.setting-input { width: 100% !important; }

    /* Tables scroll horizontally */
    table { display: block; overflow-x: auto; }

    /* Cards tighter on mobile */
    .card { padding: 14px; }
    .card-value { font-size: 26px; }

    /* Logs shorter */
    .log-container { height: 400px; font-size: 12px; }

    /* Login responsive */
    .login-box { width: 90%; max-width: 360px; padding: 24px; }
}
"""

# ── Sidebar JS ───────────────────────────────────────────────────
SIDEBAR_JS = """
<div class="sidebar-backdrop" id="sidebar-backdrop" onclick="closeMobileSidebar()"></div>
<button class="mobile-hamburger" id="mobile-hamburger" onclick="openMobileSidebar()">☰</button>
<script>
const sidebar = document.getElementById('sidebar');
const main = document.getElementById('main');
const backdrop = document.getElementById('sidebar-backdrop');
const mobileHam = document.getElementById('mobile-hamburger');

function isMobile() { return window.innerWidth <= 768; }

function toggleSidebar() {
    if (isMobile()) {
        openMobileSidebar();
    } else {
        sidebar.classList.toggle('collapsed');
        main.classList.toggle('shifted');
        localStorage.setItem('sidebar-collapsed', sidebar.classList.contains('collapsed'));
    }
}
function openMobileSidebar() {
    sidebar.classList.add('mobile-open');
    backdrop.classList.add('active');
    if (mobileHam) mobileHam.style.display = 'none';
}
function closeMobileSidebar() {
    sidebar.classList.remove('mobile-open');
    backdrop.classList.remove('active');
    if (mobileHam) mobileHam.style.display = '';
}

// Desktop: restore collapsed state
if (!isMobile() && localStorage.getItem('sidebar-collapsed') === 'true') {
    sidebar.classList.add('collapsed');
    main.classList.add('shifted');
}

// Close sidebar on nav click (mobile)
document.querySelectorAll('.sidebar-nav a, .sidebar-footer a').forEach(function(link) {
    link.addEventListener('click', function() {
        if (isMobile()) closeMobileSidebar();
    });
});

// Handle resize between mobile/desktop
window.addEventListener('resize', function() {
    if (!isMobile()) {
        sidebar.classList.remove('mobile-open');
        backdrop.classList.remove('active');
        if (mobileHam) mobileHam.style.display = '';
    }
});

// Hide mobile hamburger on desktop
if (!isMobile() && mobileHam) mobileHam.style.display = 'none';
</script>
"""

# ── HTML Templates ───────────────────────────────────────────────
def _sidebar(active="home"):
    def cls(page):
        return ' active' if page == active else ''
    return f"""
    <aside class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <button class="toggle-btn" id="toggle-btn" onclick="toggleSidebar()" title="Toggle sidebar">❖</button>
            <div class="sidebar-brand">Oath <span>Bot</span></div>
        </div>
        <nav class="sidebar-nav">
            <div class="sidebar-nav-title">General</div>
            <a href="/" class="{cls('home')}"><span class="icon">⊞</span><span class="label">Dashboard</span></a>
            <a href="/calendar" class="{cls('calendar')}"><span class="icon">📅</span><span class="label">Calendar</span></a>
            <a href="/users" class="{cls('users')}"><span class="icon">👥</span><span class="label">Users</span></a>
            
            <div class="sidebar-nav-title" style="margin-top:10px">System</div>
            <a href="/battle" class="{cls('battle')}"><span class="icon">⚔️</span><span class="label">Battle Cards</span></a>
            <a href="/dinolb" class="{cls('dinolb')}"><span class="icon">🏆</span><span class="label">Leaderboard</span></a>
            <a href="/logs" class="{cls('logs')}"><span class="icon">▤</span><span class="label">System Logs</span></a>
            <a href="/settings" class="{cls('settings')}"><span class="icon">⚙</span><span class="label">Settings</span></a>
        </nav>
        <div class="sidebar-footer">
            <a href="/logout"><span class="icon">⎋</span><span class="label">Secure Logout</span></a>
        </div>
    </aside>"""

def _page(title, content, active="home"):
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<title>{title} — Oath Bot</title>
<style>{CSS}</style>
</head><body>
{_sidebar(active)}
<div class="main" id="main">
{content}
</div>
<div class="toast" id="toast"></div>
{SIDEBAR_JS}
</body></html>"""

LOGIN_PAGE = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Oath Bot</title>
<style>{CSS}</style>
</head><body>
<div class="login-wrap">
    <div class="login-box">
        <h2>❖ Oath Scheduler</h2>
        <div class="login-error" id="err">Invalid credentials</div>
        <form method="POST" action="/login">
            <input type="password" name="password" placeholder="Administrator Password" autofocus required>
            <button type="submit" class="btn btn-primary" style="letter-spacing:0.5px">AUTHENTICATE</button>
        </form>
    </div>
</div>
</body></html>"""

# ── Route Handlers ───────────────────────────────────────────────
routes = web.RouteTableDef()

@routes.get("/login")
async def login_page(request):
    return web.Response(text=LOGIN_PAGE, content_type="text/html")

@routes.post("/login")
async def login_post(request):
    data = await request.post()
    pw = data.get("password", "")
    if pw == ADMIN_PASSWORD:
        token = secrets.token_hex(32)
        SESSION_TOKENS[token] = time.time() + TOKEN_TTL
        resp = web.HTTPFound("/")
        resp.set_cookie("session", token, max_age=TOKEN_TTL, httponly=True, samesite="Lax")
        return resp
    err_page = LOGIN_PAGE.replace('display: none', 'display: block')
    return web.Response(text=err_page, content_type="text/html")

@routes.get("/logout")
async def logout(request):
    token = request.cookies.get("session")
    SESSION_TOKENS.pop(token, None)
    resp = web.HTTPFound("/login")
    resp.del_cookie("session")
    return resp

# ── Dashboard Home ───────────────────────────────────────────────
@routes.get("/")
async def dashboard_home(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    g = _state_getters
    session_name = g.get("session_name", lambda: "None")()
    session_dt_str = g.get("session_dt_str", lambda: None)()
    session_ended = g.get("session_ended", lambda: False)()
    attending = g.get("attending_ids", lambda: [])()
    standby = g.get("standby_ids", lambda: [])()
    not_attending = g.get("not_attending_ids", lambda: [])()
    checked_in = g.get("checked_in_ids", lambda: [])()
    checkin_active = g.get("checkin_active", lambda: False)()
    history = g.get("attendance_history", lambda: {})()

    # Status
    if session_ended:
        status_dot = "dot-red"
        status_text = "Session Ended"
    elif session_dt_str:
        try:
            dt = datetime.fromisoformat(session_dt_str)
            if datetime.now(dt.tzinfo) >= dt:
                status_dot = "dot-orange"
                status_text = "Session Live"
            else:
                status_dot = "dot-green"
                status_text = f"Upcoming — {dt.strftime('%b %d %I:%M %p')}"
        except:
            status_dot = "dot-green"
            status_text = "Scheduled"
    else:
        status_dot = "dot-red"
        status_text = "No Active Session"

    # Build attending list HTML (CACHED - no more API lag)
    attend_names = await _resolve_names(attending)
    attend_html = ""
    for uid in attending:
        name = attend_names.get(uid, str(uid))
        check = ' <span style="color:var(--green)">✅</span>' if uid in checked_in else ""
        attend_html += f'<li><span class="dot dot-green"></span>{name}{check}</li>'
    if not attend_html:
        attend_html = '<li style="color:var(--text-dim)">No one yet</li>'

    standby_names = await _resolve_names(standby)
    standby_html = ""
    for uid in standby:
        name = standby_names.get(uid, str(uid))
        standby_html += f'<li><span class="dot dot-orange"></span>{name}</li>'
    if not standby_html:
        standby_html = '<li style="color:var(--text-dim)">Empty</li>'

    content = f"""
    <div class="container">
        <div class="grid grid-3" style="margin-bottom:16px">
            <div class="card">
                <div class="card-header">Session Status</div>
                <div class="card-value" style="font-size:18px"><span class="dot {status_dot}"></span>{status_text}</div>
                <div style="margin-top:8px;font-size:13px;color:var(--text-dim)">{session_name}</div>
            </div>
            <div class="card">
                <div class="card-header">Attending</div>
                <div class="card-value green">{len(attending)}</div>
                <div style="margin-top:4px;font-size:13px;color:var(--text-dim)">Checked in: {len(checked_in)}</div>
            </div>
            <div class="card">
                <div class="card-header">Total Users Tracked</div>
                <div class="card-value">{len(history)}</div>
                <div style="margin-top:4px;font-size:13px;color:var(--text-dim)">Standby: {len(standby)}</div>
            </div>
        </div>
        <div class="grid grid-2">
            <div class="card">
                <div class="card-header">✅ Attending ({len(attending)})</div>
                <ul class="user-list">{attend_html}</ul>
            </div>
            <div class="card">
                <div class="card-header">⏳ Standby ({len(standby)})</div>
                <ul class="user-list">{standby_html}</ul>
            </div>
        </div>
    </div>
    <script>setTimeout(()=>location.reload(), 30000);</script>"""

    return web.Response(text=_page("Dashboard", content, "home"), content_type="text/html")

# ── Users Page ───────────────────────────────────────────────────
@routes.get("/users")
async def users_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    history = _state_getters.get("attendance_history", lambda: {})()

    rows = ""
    for uid_str, stats in sorted(history.items(), key=lambda x: x[1].get("attended", 0), reverse=True):
        attended = stats.get("attended", 0)
        no_shows = stats.get("no_shows", 0)
        total = stats.get("total_signups", 0)
        streak = stats.get("streak", 0)
        best = stats.get("best_streak", 0)
        rate = (attended / total * 100) if total > 0 else 0

        # Name resolution (CACHED)
        name = await _resolve_name(uid_str)

        rate_cls = "badge-green" if rate >= 80 else ("badge-orange" if rate >= 50 else "badge-red")
        ns_cls = "badge-red" if no_shows > 0 else "badge-green"

        rows += f"""<tr>
            <td><strong>{name}</strong><br><span style="font-size:11px;color:var(--text-dim)">{uid_str}</span></td>
            <td>{attended}</td>
            <td><span class="badge {ns_cls}">{no_shows}</span></td>
            <td>{total}</td>
            <td><span class="badge {rate_cls}">{rate:.0f}%</span></td>
            <td>{streak} <span style="color:var(--text-dim);font-size:12px">(best: {best})</span></td>
            <td><button class="btn btn-danger btn-sm" onclick="resetUser('{uid_str}')">Reset</button></td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:24px">No user data yet</td></tr>'

    content = f"""
    <div class="container">
        <div class="card">
            <div class="card-header">User Attendance Stats</div>
            <table>
                <thead><tr>
                    <th>User</th><th>Attended</th><th>No-Shows</th><th>Signups</th><th>Rate</th><th>Streak</th><th>Actions</th>
                </tr></thead>
                <tbody>{rows}</tbody>
            </table>
        </div>
    </div>
    <script>
    function resetUser(uid) {{
        if (!confirm('Reset all stats for user ' + uid + '?')) return;
        fetch('/api/reset-stats', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{user_id: uid}})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{ showToast('Stats reset for ' + (d.name || uid)); location.reload(); }}
            else {{ alert(d.error || 'Failed'); }}
        }});
    }}
    function showToast(msg) {{
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 3000);
    }}
    </script>"""

    return web.Response(text=_page("Users", content, "users"), content_type="text/html")

# ── Logs Page ────────────────────────────────────────────────────
@routes.get("/logs")
async def logs_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    content = """
    <div class="container">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <h3 style="color:var(--text-bright)">Live Logs</h3>
            <div>
                <button class="btn btn-primary btn-sm" onclick="clearLogs()">Clear View</button>
                <span style="font-size:12px;color:var(--text-dim);margin-left:8px" id="conn-status">Connecting...</span>
            </div>
        </div>
        <div class="log-container" id="logs"></div>
    </div>
    <script>
    const logsEl = document.getElementById('logs');
    const connEl = document.getElementById('conn-status');
    function addLine(text) {
        const div = document.createElement('div');
        div.className = 'log-line';
        if (text.includes('❌') || text.includes('ERROR')) div.className += ' error';
        else if (text.includes('✅')) div.className += ' success';
        // Highlight timestamp
        const m = text.match(/^\\[([^\\]]+)\\]/);
        if (m) {
            div.innerHTML = '<span class="ts">[' + m[1] + ']</span>' + text.slice(m[0].length);
        } else {
            div.textContent = text;
        }
        logsEl.appendChild(div);
        logsEl.scrollTop = logsEl.scrollHeight;
    }
    function clearLogs() { logsEl.innerHTML = ''; }

    // Load existing logs
    fetch('/api/logs').then(r=>r.json()).then(d=>{
        d.logs.forEach(l => addLine(l));
    });

    // SSE for live updates
    const es = new EventSource('/api/logs/stream');
    es.onopen = () => { connEl.textContent = '🟢 Connected'; connEl.style.color = 'var(--green)'; };
    es.onmessage = (e) => addLine(e.data);
    es.onerror = () => { connEl.textContent = '🔴 Disconnected'; connEl.style.color = 'var(--red)'; };
    </script>"""

    return web.Response(text=_page("Logs", content, "logs"), content_type="text/html")


@routes.get("/dinolb")
async def dinolb_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    # ── Battle Leaderboard ──
    lb = _state_getters.get("load_dino_lb", lambda: {})()
    
    battle_rows = ""
    rank = 1
    for uid_str, stats in sorted(lb.items(), key=lambda x: x[1].get("wins", 0), reverse=True):
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)
        ties = stats.get("ties", 0)
        streak = stats.get("streak", 0)
        best = stats.get("best_streak", 0)
        
        name = await _resolve_name(uid_str)

        battle_rows += f"""<tr>
            <td><strong>#{rank}</strong></td>
            <td><strong>{name}</strong><br><span style="font-size:11px;color:var(--text-dim)">{uid_str}</span></td>
            <td style="color:var(--green);font-weight:bold;">{wins}</td>
            <td style="color:var(--red);">{losses}</td>
            <td style="color:var(--text-dim);">{ties}</td>
            <td>🔥 {streak}</td>
            <td style="color:var(--text-dim);">Best: {best}</td>
        </tr>"""
        rank += 1

    if not battle_rows:
        battle_rows = '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:24px">No betting data on record yet. Be the first!</td></tr>'

    # ── Attendance Leaderboard ──
    history = _state_getters.get("attendance_history", lambda: {})()
    
    attend_rows = ""
    noshow_rows = ""
    rank_a = 1
    rank_n = 1
    
    # Build attendance data
    attend_data = []
    noshow_data = []
    for uid_str, user_hist in history.items():
        total = user_hist.get("total", 0)
        attended = user_hist.get("attended", 0)
        noshows = user_hist.get("noshows", 0)
        checked_in = user_hist.get("checked_in", 0)
        rate = round((attended / total * 100) if total > 0 else 0, 1)
        attend_data.append((uid_str, total, attended, checked_in, noshows, rate))
        if noshows > 0:
            noshow_data.append((uid_str, noshows, total, rate))
    
    # Sort attendance by sessions attended descending
    for uid_str, total, attended, checked_in, noshows, rate in sorted(attend_data, key=lambda x: x[2], reverse=True):
        name = await _resolve_name(uid_str)
        rate_color = "var(--green)" if rate >= 75 else ("var(--text-dim)" if rate >= 50 else "var(--red)")
        attend_rows += f"""<tr>
            <td><strong>#{rank_a}</strong></td>
            <td><strong>{name}</strong><br><span style="font-size:11px;color:var(--text-dim)">{uid_str}</span></td>
            <td style="font-weight:bold;">{total}</td>
            <td style="color:var(--green);font-weight:bold;">{attended}</td>
            <td>{checked_in}</td>
            <td style="color:var(--red);">{noshows}</td>
            <td style="color:{rate_color};font-weight:bold;">{rate}%</td>
        </tr>"""
        rank_a += 1

    if not attend_rows:
        attend_rows = '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:24px">No attendance data recorded yet.</td></tr>'

    # Sort no-shows by count descending
    for uid_str, noshows, total, rate in sorted(noshow_data, key=lambda x: x[1], reverse=True):
        name = await _resolve_name(uid_str)
        noshow_rate = round((noshows / total * 100) if total > 0 else 0, 1)
        noshow_rows += f"""<tr>
            <td><strong>#{rank_n}</strong></td>
            <td><strong>{name}</strong><br><span style="font-size:11px;color:var(--text-dim)">{uid_str}</span></td>
            <td style="color:var(--red);font-weight:bold;">{noshows}</td>
            <td>{total}</td>
            <td style="color:var(--red);">{noshow_rate}%</td>
        </tr>"""
        rank_n += 1

    if not noshow_rows:
        noshow_rows = '<tr><td colspan="5" style="text-align:center;color:var(--text-dim);padding:24px">No no-shows recorded. Everyone is showing up!</td></tr>'

    content = f"""
    <style>
    .lb-tabs {{ display: flex; gap: 4px; margin-bottom: 20px; background: var(--bg3); padding: 4px; border-radius: 10px; }}
    .lb-tab {{ flex: 1; padding: 10px 16px; text-align: center; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; color: var(--text-dim); transition: all 0.2s; border: none; background: none; }}
    .lb-tab:hover {{ color: var(--text-bright); background: rgba(88,101,242,0.1); }}
    .lb-tab.active {{ background: var(--accent); color: white; box-shadow: 0 2px 8px rgba(88,101,242,0.3); }}
    .lb-panel {{ display: none; }}
    .lb-panel.active {{ display: block; }}
    </style>
    <div class="container">
        <div class="lb-tabs">
            <button class="lb-tab active" onclick="switchLbTab('battles')">🦖 Battles</button>
            <button class="lb-tab" onclick="switchLbTab('attendance')">📊 Attendance</button>
            <button class="lb-tab" onclick="switchLbTab('noshows')">❌ No-Shows</button>
        </div>

        <div class="lb-panel active" id="panel-battles">
            <div class="card">
                <div class="card-header">🦖 Dino Battle Leaderboard 🦕</div>
                <table>
                    <thead><tr>
                        <th>Rank</th><th>Bettor</th><th>Wins</th><th>Losses</th><th>Ties</th><th>Win Streak</th><th>Best Streak</th>
                    </tr></thead>
                    <tbody>{battle_rows}</tbody>
                </table>
            </div>
        </div>

        <div class="lb-panel" id="panel-attendance">
            <div class="card">
                <div class="card-header">📊 Attendance Leaderboard</div>
                <table>
                    <thead><tr>
                        <th>Rank</th><th>Member</th><th>Sessions</th><th>Attended</th><th>Checked In</th><th>No-Shows</th><th>Rate</th>
                    </tr></thead>
                    <tbody>{attend_rows}</tbody>
                </table>
            </div>
        </div>

        <div class="lb-panel" id="panel-noshows">
            <div class="card">
                <div class="card-header">❌ No-Show Wall of Shame</div>
                <table>
                    <thead><tr>
                        <th>Rank</th><th>Member</th><th>No-Shows</th><th>Total Sessions</th><th>No-Show Rate</th>
                    </tr></thead>
                    <tbody>{noshow_rows}</tbody>
                </table>
            </div>
        </div>
    </div>
    <script>
    function switchLbTab(tab) {{
        document.querySelectorAll('.lb-tab').forEach(function(t) {{ t.classList.remove('active'); }});
        document.querySelectorAll('.lb-panel').forEach(function(p) {{ p.classList.remove('active'); }});
        document.getElementById('panel-' + tab).classList.add('active');
        event.target.classList.add('active');
    }}
    </script>
    """

    return web.Response(text=_page("Leaderboard", content, "dinolb"), content_type="text/html")

# ── Battle Cards Page ────────────────────────────────────────────
@routes.get("/battle")
async def battle_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    load_dinos = _state_getters.get("load_dinos")
    if load_dinos:
        all_dinos = load_dinos()
    else:
        all_dinos = []

    cards_html = ""
    for d in all_dinos:
        bg_col = "var(--red)" if d['type'] == 'carnivore' else "var(--green)"
        safe_name = str(d['name']).replace('"', '&quot;')
        safe_id = str(d['id']).replace('"', '&quot;')
        lore_preview = str(d.get('lore', '')).replace('"', '&quot;')[:60]
        if lore_preview:
            lore_preview += '...'
        else:
            lore_preview = 'No lore set'
        
        cards_html += f"""
        <div class="card dino-card" data-diet="{d['type']}" style="border-top-color:{bg_col};cursor:pointer" onclick="window.location='/dino/{safe_id}'">
            <div class="card-actions">
                <button class="btn btn-danger btn-sm" style="padding:2px 8px;font-size:11px" onclick="event.stopPropagation();deleteCard('{safe_id}', '{safe_name}')">Delete</button>
            </div>
            <div style="display:flex;gap:14px;align-items:center;margin-bottom:10px">
                <div style="width:72px;height:72px;border-radius:50%;overflow:hidden;flex-shrink:0;border:2px solid {bg_col};background:var(--bg3)">
                    <img src="/assets/dinos/{safe_id}.png" style="width:100%;height:100%;object-fit:cover" onerror="this.src='/assets/dinos/defaults/{safe_id}.png';this.onerror=function(){{this.style.display='none';this.parentElement.innerHTML='<div style=&quot;width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:28px&quot;>🦕</div>'}}">
                </div>
                <div style="flex:1;min-width:0">
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
                        <div style="font-weight:700;font-size:16px;color:var(--text-bright);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{safe_name}</div>
                        <div class="badge" style="background:rgba({'240,71,71' if d['type']=='carnivore' else '67,181,129'},0.2);color:{bg_col};font-size:11px;flex-shrink:0">{d['type'].upper()}</div>
                    </div>
                    <div style="color:var(--text-dim);font-size:12px;font-style:italic;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{lore_preview}</div>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:4px;font-size:12px;text-align:center">
                <div style="background:var(--bg3);padding:4px;border-radius:4px">CW <strong style="color:var(--text-bright)">{d.get('cw', 3000)}</strong></div>
                <div style="background:var(--bg3);padding:4px;border-radius:4px">HP <strong style="color:var(--green)">{d.get('hp', 500)}</strong></div>
                <div style="background:var(--bg3);padding:4px;border-radius:4px">ATK <strong style="color:var(--red)">{d.get('atk', 50)}</strong></div>
                <div style="background:var(--bg3);padding:4px;border-radius:4px">DEF <strong style="color:var(--accent)">{d.get('armor', 1.0)}</strong></div>
                <div style="background:var(--bg3);padding:4px;border-radius:4px">SPD <strong style="color:#f1c40f">{d.get('spd', 500)}</strong></div>
            </div>
        </div>
        """

    if not cards_html:
        cards_html = '<div style="color:var(--text-dim);width:100%;text-align:center;padding:40px">No dinosaur profiles yet. Create one below.</div>'

    carni_count = sum(1 for d in all_dinos if d['type'] == 'carnivore')
    herbi_count = sum(1 for d in all_dinos if d['type'] == 'herbivore')

    content = f"""
    <style>
    .diet-tabs {{ display: flex; gap: 4px; margin-bottom: 20px; background: var(--bg3); padding: 4px; border-radius: 10px; }}
    .diet-tab {{ flex: 1; padding: 10px 16px; text-align: center; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 14px; color: var(--text-dim); transition: all 0.2s; border: none; background: none; }}
    .diet-tab:hover {{ color: var(--text-bright); background: rgba(88,101,242,0.1); }}
    .diet-tab.active {{ background: var(--accent); color: white; box-shadow: 0 2px 8px rgba(88,101,242,0.3); }}
    </style>
    <div class="container">
        <!-- Global Frames Upload Module -->
        <div class="card" style="margin-bottom:24px;border-left:4px solid var(--accent)">
            <h3 style="margin-bottom:12px">⚔️ Global Battle Frames</h3>
            <p style="color:var(--text-dim);font-size:13px;margin-bottom:16px">Upload a specific transparent layout overlay for the Left (Attacker) and Right (Defender) cards. All Dinosaur profiles will automatically use these two master frames during combat generation.</p>
            <div class="grid grid-2" style="gap:16px">
                <form id="leftFrameForm" enctype="multipart/form-data">
                    <div style="background:var(--bg3);padding:16px;border-radius:var(--radius);border:1px dashed var(--border)">
                        <h4 style="margin-bottom:8px;color:var(--text-bright)">Card A (Left)</h4>
                        <input type="hidden" name="side" value="left">
                        <input type="file" name="frame" accept="image/png" required style="width:100%;margin-bottom:12px;font-size:13px">
                        <button type="submit" class="btn btn-primary btn-sm" style="width:100%" id="leftFrameBtn">Upload Left Frame</button>
                    </div>
                </form>
                <form id="rightFrameForm" enctype="multipart/form-data">
                    <div style="background:var(--bg3);padding:16px;border-radius:var(--radius);border:1px dashed var(--border)">
                        <h4 style="margin-bottom:8px;color:var(--text-bright)">Card B (Right)</h4>
                        <input type="hidden" name="side" value="right">
                        <input type="file" name="frame" accept="image/png" required style="width:100%;margin-bottom:12px;font-size:13px">
                        <button type="submit" class="btn btn-primary btn-sm" style="width:100%" id="rightFrameBtn">Upload Right Frame</button>
                    </div>
                </form>
            </div>
        </div>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <h2 style="color:var(--text-bright);margin:0">🦖 Dinosaur Profiles ({len(all_dinos)})</h2>
            <button class="btn btn-success" onclick="document.getElementById('uploadModal').classList.add('active')">+ Create Profile</button>
        </div>
        
        <div class="diet-tabs" style="margin-bottom:20px">
            <button class="diet-tab active" onclick="filterDinos('all', this)">All ({len(all_dinos)})</button>
            <button class="diet-tab" onclick="filterDinos('carnivore', this)">🥩 Carnivores ({carni_count})</button>
            <button class="diet-tab" onclick="filterDinos('herbivore', this)">🌿 Herbivores ({herbi_count})</button>
        </div>

        <div class="grid" id="dinoGrid" style="grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));gap:16px">
            {cards_html}
        </div>
    </div>

    <!-- Upload Modal -->
    <div class="modal-overlay" id="uploadModal">
        <div class="modal" style="width:550px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
                <h3 style="margin:0">Create Dinosaur Profile</h3>
                <button class="btn btn-secondary btn-sm" onclick="randomizeStats()" style="background:var(--accent);color:white;border:none" title="Generate authentic Path of Titans stats mapped to Diet">🎲 Random Stats</button>
            </div>
            <p style="color:var(--text-dim);margin-bottom:16px;font-size:13px;line-height:1.4">Create a custom creature profile. Upload a character portrait ('Avatar') and define their base combat statistics.</p>
            
            <form id="uploadForm" enctype="multipart/form-data">
                <div class="grid grid-2" style="gap:12px;margin-bottom:12px">
                    <div class="form-group" style="grid-column:span 2">
                        <label>Character Avatar (PNG/JPG):</label>
                        <input type="file" id="imageFile" name="image" accept="image/*" required style="width:100%;padding:7px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:var(--radius)">
                    </div>
                </div>

                <div class="grid grid-2" style="gap:12px">
                    <div class="form-group">
                        <label>ID (unique, no spaces):</label>
                        <input type="text" id="dinoId" name="id" required placeholder="super_rex" pattern="[a-zA-Z0-9_]+" style="width:100%">
                    </div>
                    <div class="form-group">
                        <label>Display Name:</label>
                        <input type="text" id="dinoName" name="name" required placeholder="Super Rex" style="width:100%">
                    </div>
                </div>
                
                <div class="form-group">
                    <label>Diet Type:</label>
                    <select id="dinoType" name="type" required style="width:100%">
                        <option value="carnivore">Carnivore</option>
                        <option value="herbivore">Herbivore</option>
                        <option value="aquatic">Aquatic</option>
                        <option value="flyer">Flyer</option>
                    </select>
                </div>

                <div class="form-group">
                    <label>Dino Lore / Description (Optional):</label>
                    <textarea id="dinoLore" name="lore" rows="3" placeholder="Enter the backstory or details about this dinosaur..." style="width:100%;padding:10px;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);font-family:inherit;resize:vertical"></textarea>
                </div>

                <div class="grid grid-2" style="gap:12px">
                    <div class="form-group">
                        <label>Combat Weight (CW):</label>
                        <input type="number" id="dinoCW" name="cw" required value="3000" min="100" style="width:100%">
                    </div>
                    <div class="form-group">
                        <label>Health (HP):</label>
                        <input type="number" id="dinoHP" name="hp" required value="500" min="10" style="width:100%">
                    </div>
                    <div class="form-group">
                        <label>Attack Damage (ATK):</label>
                        <input type="number" id="dinoATK" name="atk" required value="50" min="1" style="width:100%">
                    </div>
                    <div class="form-group">
                        <label>Armor (DEF multiplier):</label>
                        <input type="number" id="dinoArmor" name="armor" step="0.1" required value="1.0" min="0.1" style="width:100%">
                    </div>
                    <div class="form-group">
                        <label>Speed (SPD initative):</label>
                        <input type="number" id="dinoSPD" name="spd" required value="500" min="10" style="width:100%">
                    </div>
                </div>
                
                <div class="modal-actions" style="margin-top:20px;display:flex;gap:12px;justify-content:flex-end">
                    <button type="button" class="btn btn-secondary" onclick="document.getElementById('uploadModal').classList.remove('active')">Cancel</button>
                    <button type="submit" class="btn btn-primary" id="uploadBtn">Save Card</button>
                </div>
            </form>
        </div>
    </div>

    <script>
    function randomizeStats() {{
        const diet = document.getElementById('dinoType').value;
        let cw, hp, atk, armor, spd;
        
        // Authentic simulated Stat Generator based on Diet brackets
        if (diet === 'carnivore') {{
            cw = Math.floor(Math.random() * 4000) + 2500;
            hp = Math.floor(cw / 6.5);
            atk = Math.floor(Math.random() * 40) + 60;
            armor = (Math.random() * 0.5 + 0.8).toFixed(1);
            spd = Math.floor(Math.random() * 400) + 700;
        }} else if (diet === 'herbivore') {{
            cw = Math.floor(Math.random() * 5000) + 3500;
            hp = Math.floor(cw / 5.5);
            atk = Math.floor(Math.random() * 30) + 50;
            armor = (Math.random() * 0.6 + 1.2).toFixed(1);
            spd = Math.floor(Math.random() * 200) + 500;
        }} else if (diet === 'aquatic') {{
            cw = Math.floor(Math.random() * 5000) + 4000;
            hp = Math.floor(cw / 6.0);
            atk = Math.floor(Math.random() * 50) + 70;
            armor = (Math.random() * 0.4 + 1.0).toFixed(1);
            spd = Math.floor(Math.random() * 300) + 800;
        }} else {{
            cw = Math.floor(Math.random() * 1500) + 1000;
            hp = Math.floor(cw / 4.0);
            atk = Math.floor(Math.random() * 20) + 30;
            armor = (Math.random() * 0.2 + 0.5).toFixed(1);
            spd = Math.floor(Math.random() * 500) + 1000;
        }}
        
        document.getElementById('dinoCW').value = cw;
        document.getElementById('dinoHP').value = hp;
        document.getElementById('dinoATK').value = atk;
        document.getElementById('dinoArmor').value = armor;
        document.getElementById('dinoSPD').value = spd;
    }}

    async function uploadGlobalFrame(e, formId, btnId) {{
        e.preventDefault();
        const btn = document.getElementById(btnId);
        btn.disabled = true;
        btn.textContent = "Uploading...";
        try {{
            const formData = new FormData(e.target);
            const r = await fetch('/api/upload-global-frame', {{
                method: 'POST', body: formData
            }});
            const data = await r.json();
            if(data.ok) {{
                showToast("Global Frame uploaded successfully!");
                btn.textContent = "Uploaded!";
            }} else {{
                alert("Error: " + data.error);
                btn.textContent = "Upload Frame";
            }}
        }} catch(err) {{
            alert(err);
            btn.textContent = "Upload Frame";
        }}
        btn.disabled = false;
    }}
    document.getElementById('leftFrameForm').addEventListener('submit', (e) => uploadGlobalFrame(e, 'leftFrameForm', 'leftFrameBtn'));
    document.getElementById('rightFrameForm').addEventListener('submit', (e) => uploadGlobalFrame(e, 'rightFrameForm', 'rightFrameBtn'));

    document.getElementById('uploadForm').onsubmit = async (e) => {{
        e.preventDefault();
        const btn = document.getElementById('uploadBtn');
        btn.disabled = true;
        btn.textContent = "Uploading...";
        
        try {{
            const formData = new FormData(e.target);
            const r = await fetch('/api/upload-card', {{
                method: 'POST',
                body: formData
            }});
            const data = await r.json();
            if(data.ok) {{
                showToast("Card uploaded successfully!");
                location.reload();
            }} else {{
                alert("Error: " + data.error);
                btn.disabled = false;
                btn.textContent = "Save Card";
            }}
        }} catch(err) {{
            alert(err);
            btn.disabled = false;
            btn.textContent = "Save Card";
        }}
    }};

    function deleteCard(id, name) {{
        if(!confirm("Delete " + name + " (ID: " + id + ")? This cannot be undone.")) return;
        fetch('/api/delete-card', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{id: id}})
        }}).then(r=>r.json()).then(d=>{{
            if(d.ok) {{
                showToast("Deleted " + name);
                location.reload();
            }} else {{
                alert(d.error);
            }}
        }});
    }}

    function filterDinos(diet, btn) {{
        document.querySelectorAll('.diet-tab').forEach(function(t) {{ t.classList.remove('active'); }});
        btn.classList.add('active');
        document.querySelectorAll('#dinoGrid .dino-card').forEach(function(card) {{
            if (diet === 'all' || card.dataset.diet === diet) {{
                card.style.display = '';
            }} else {{
                card.style.display = 'none';
            }}
        }});
    }}
    </script>
    """
    return web.Response(text=_page("Battle Cards", content, "battle"), content_type="text/html")

# ── Dino Profile Page ────────────────────────────────────────────
@routes.get("/dino/{dino_id}")
async def dino_profile_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    dino_id = request.match_info["dino_id"]
    load_dinos = _state_getters.get("load_dinos")
    if not load_dinos:
        raise web.HTTPFound("/battle")

    all_dinos = load_dinos()
    dino = None
    for d in all_dinos:
        if d['id'] == dino_id:
            dino = d
            break

    if not dino:
        raise web.HTTPFound("/battle")

    bg_col = "var(--red)" if dino['type'] == 'carnivore' else "var(--green)"
    diet_label = dino['type'].capitalize()
    lore = dino.get('lore', '')
    safe_lore = str(lore).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # Build abilities & traits info from battle engine data
    import battle_engine as _be
    import json as _json

    dino_family = _be.SPECIES_FAMILIES.get(dino_id.lower(), "generic")
    family_label = dino_family.replace("_", " ").title()
    cw_val = dino.get('cw', 3000)
    group_slots = _be.get_group_slots(cw_val)
    passive = _be.PASSIVES.get(dino_family)

    # Use custom abilities if stored, otherwise generate from family pool
    if dino.get('custom_abilities'):
        abilities = dino['custom_abilities']
    else:
        abilities = _be.get_ability_pool(dino_family, dino['type'], 100)

    # Serialize abilities to JSON for JS
    abilities_json = _json.dumps(abilities).replace("'", "\\'")

    # Passive HTML
    passive_html = ""
    if passive:
        passive_html = f"""
        <div style="padding:12px;background:rgba(241,196,15,0.08);border-radius:8px;border-left:3px solid #f1c40f;margin-bottom:16px">
            <div style="font-weight:700;color:#f1c40f;font-size:14px;margin-bottom:2px">✨ {passive[0]}</div>
            <div style="color:var(--text-dim);font-size:13px">{passive[1]}</div>
        </div>"""

    traits_html = f"""
        <div class="card" style="margin-top:20px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                <h3 style="margin:0;font-weight:700;color:var(--text-bright)">Abilities & Traits</h3>
                <button onclick="addAbility()" class="btn btn-primary" style="font-size:12px;padding:6px 14px">+ Add Ability</button>
            </div>
            <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
                <div style="padding:8px 14px;background:var(--bg);border-radius:8px;font-size:13px">
                    <span style="color:var(--text-dim)">Family:</span> <span style="color:var(--text-bright);font-weight:600">{family_label}</span>
                </div>
                <div style="padding:8px 14px;background:var(--bg);border-radius:8px;font-size:13px">
                    <span style="color:var(--text-dim)">CW:</span> <span style="color:#9b59b6;font-weight:600">{cw_val}</span>
                </div>
                <div style="padding:8px 14px;background:var(--bg);border-radius:8px;font-size:13px">
                    <span style="color:var(--text-dim)">Group Slots:</span> <span style="color:#3498db;font-weight:600">{group_slots}</span>
                </div>
            </div>
            {passive_html}
            <div id="abilitiesList" style="display:flex;flex-direction:column;gap:10px">
            </div>
        </div>"""

    # Check if avatar exists
    avatar_url = f"/assets/dinos/{dino_id}.png"

    content = f"""
    <div class="container" style="max-width:800px">
        <div style="margin-bottom:20px">
            <a href="/battle" style="color:var(--text-dim);font-size:13px;text-decoration:none;display:inline-flex;align-items:center;gap:6px">
                <span>\u2190</span> Back to Roster
            </a>
        </div>

        <div class="card" style="border-top:4px solid {bg_col};overflow:visible">
            <div style="display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap">
                <!-- Avatar -->
                <div style="flex-shrink:0">
                    <div style="position:relative;width:180px;height:180px;border-radius:12px;background:var(--bg);border:2px solid var(--border);overflow:hidden;display:flex;align-items:center;justify-content:center">
                        <img id="avatarImg" src="/assets/dinos/{dino_id}.png" alt="{dino['name']}" style="width:100%;height:100%;object-fit:cover" onerror="this.src='/assets/dinos/defaults/{dino_id}.png';this.onerror=function(){{this.style.display='none';document.getElementById('noAvatarText').style.display='block'}}">
                        <div id="noAvatarText" style="display:none;color:var(--text-dim);font-size:12px;text-align:center">No Avatar</div>
                        <input type="file" id="avatarUpload" accept="image/*" style="display:none" onchange="uploadAvatar(this)">
                        <div style="position:absolute;bottom:6px;right:6px;display:flex;gap:4px">
                            <button onclick="resetAvatar()" style="width:28px;height:28px;border-radius:6px;border:none;background:rgba(231,76,60,0.85);color:#fff;font-size:12px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background 0.2s;backdrop-filter:blur(4px)" title="Reset to Default">🗑️</button>
                            <button onclick="document.getElementById('avatarUpload').click()" style="width:28px;height:28px;border-radius:6px;border:none;background:rgba(88,101,242,0.85);color:#fff;font-size:12px;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:background 0.2s;backdrop-filter:blur(4px)" title="Upload Avatar">📷</button>
                        </div>
                    </div>
                </div>

                <!-- Name & Info -->
                <div style="flex:1;min-width:200px">
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
                        <h2 style="margin:0;font-size:28px;font-weight:800;color:var(--text-bright)">{dino['name']}</h2>
                        <span class="badge" style="background:rgba({'240,71,71' if dino['type']=='carnivore' else '67,181,129'},0.2);color:{bg_col};font-size:12px;padding:4px 10px">{diet_label}</span>
                    </div>
                    <div style="color:var(--text-dim);font-size:13px;margin-bottom:16px">ID: {dino_id}</div>

                    <div class="form-group">
                        <label>Lore / Description</label>
                        <textarea id="loreInput" rows="4" style="width:100%;resize:vertical;background:var(--bg);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:10px 14px;font-family:inherit;font-size:14px">{safe_lore}</textarea>
                    </div>
                </div>
            </div>
        </div>

        <!-- Abilities & Traits -->
        {traits_html}

        <!-- Actions -->
        <div style="display:flex;gap:12px;margin-top:20px;justify-content:flex-end;flex-wrap:wrap">
            <button class="btn btn-primary" id="saveProfileBtn" onclick="saveProfile()">Save Changes</button>
            <button class="btn btn-danger" onclick="confirmDelete()">Delete Profile</button>
        </div>

        <!-- Delete Confirmation Modal -->
        <div id="deleteModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:none;align-items:center;justify-content:center">
            <div style="background:var(--card);border:2px solid #e74c3c;border-radius:12px;padding:32px;max-width:400px;text-align:center">
                <div style="font-size:48px;margin-bottom:12px">⚠️</div>
                <h3 style="color:#e74c3c;margin:0 0 12px">Delete {dino['name']}?</h3>
                <p style="color:var(--text-dim);margin:0 0 24px">This will permanently remove this dinosaur from the roster, including all custom abilities and avatar. This cannot be undone.</p>
                <div style="display:flex;gap:12px;justify-content:center">
                    <button onclick="document.getElementById('deleteModal').style.display='none'" class="btn" style="background:var(--bg3);color:var(--text);padding:8px 24px">Cancel</button>
                    <button onclick="executeDelete()" class="btn btn-danger" style="padding:8px 24px">Yes, Delete Forever</button>
                </div>
            </div>
        </div>

        <!-- Battle History (from Discord chat battles) -->
        <div class="card" style="margin-top:20px">
            <h3 style="margin-bottom:16px;font-weight:700;color:var(--text-bright)">⚔️ Battle History</h3>
            <div id="battleHistory"><span style="color:var(--text-dim)">Loading battle history...</span></div>
        </div>
    </div>

    <script>
    let dinoAbilities = {abilities_json};

    function renderAbilities() {{
        const list = document.getElementById('abilitiesList');
        list.innerHTML = '';
        dinoAbilities.forEach((ab, idx) => {{
            const mult = ab.base > 0 ? (ab.base / 100).toFixed(1) + 'x ATK' : 'Utility';
            const multColor = ab.base > 0 ? '#e74c3c' : '#3498db';
            const cdText = ab.cd > 0 ? ab.cd + 't CD' : 'No CD';
            const cdColor = ab.cd > 0 ? 'var(--text-dim)' : '#2ecc71';
            let effectBadges = '';
            (ab.effects || []).forEach(e => {{
                if (e.type === 'bleed') effectBadges += '<span style="background:rgba(231,76,60,0.2);color:#e74c3c;padding:2px 8px;border-radius:4px;font-size:11px">🩸 Bleed ' + (e.dur||0) + 't</span> ';
                else if (e.type === 'bonebreak') effectBadges += '<span style="background:rgba(241,196,15,0.2);color:#f1c40f;padding:2px 8px;border-radius:4px;font-size:11px">🦴 Break ' + (e.dur||0) + 't</span> ';
                else if (e.type === 'defense') effectBadges += '<span style="background:rgba(52,152,219,0.2);color:#3498db;padding:2px 8px;border-radius:4px;font-size:11px">🛡️ Def +' + Math.round((e.reduction||0)*100) + '%</span> ';
                else if (e.type === 'heal') effectBadges += '<span style="background:rgba(46,204,113,0.2);color:#2ecc71;padding:2px 8px;border-radius:4px;font-size:11px">💚 Heal ' + Math.round((e.pct||0)*100) + '%</span> ';
            }});
            const card = document.createElement('div');
            card.style.cssText = 'padding:12px;background:var(--bg);border-radius:8px;border-left:3px solid var(--accent)';
            card.id = 'ability-' + idx;
            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
                    <span style="font-weight:700;color:var(--text-bright);font-size:15px">⚔️ ${{ab.name}}</span>
                    <div style="display:flex;gap:8px;align-items:center">
                        <span style="color:${{multColor}};font-weight:600">${{mult}}</span>
                        <span style="color:${{cdColor}};font-size:12px">${{cdText}}</span>
                        <button onclick="editAbility(${{idx}})" style="background:none;border:none;color:var(--accent);cursor:pointer;font-size:14px;padding:2px" title="Edit">✏️</button>
                        <button onclick="deleteAbility(${{idx}})" style="background:none;border:none;color:var(--red);cursor:pointer;font-size:14px;padding:2px" title="Delete">🗑️</button>
                    </div>
                </div>
                <div style="color:var(--text-dim);font-size:13px;font-style:italic;margin-bottom:4px">${{ab.desc}}</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap">${{effectBadges}}</div>
            `;
            list.appendChild(card);
        }});
    }}

    function deleteAbility(idx) {{
        if (!confirm('Delete "' + dinoAbilities[idx].name + '"?')) return;
        dinoAbilities.splice(idx, 1);
        renderAbilities();
    }}

    function editAbility(idx) {{
        const ab = dinoAbilities[idx];
        const card = document.getElementById('ability-' + idx);
        const eff = ab.effects && ab.effects[0] ? ab.effects[0] : {{}};
        card.innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
                <input id="ed-name-${{idx}}" value="${{ab.name}}" placeholder="Name" style="padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                <input id="ed-desc-${{idx}}" value="${{ab.desc}}" placeholder="Description" style="padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:8px;margin-bottom:8px">
                <div>
                    <label style="font-size:11px;color:var(--text-dim)">DMG (base)</label>
                    <input id="ed-base-${{idx}}" type="number" value="${{ab.base}}" style="width:100%;padding:6px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-dim)">Cooldown</label>
                    <input id="ed-cd-${{idx}}" type="number" value="${{ab.cd}}" style="width:100%;padding:6px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-dim)">Effect</label>
                    <select id="ed-eff-${{idx}}" style="width:100%;padding:6px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                        <option value="">None</option>
                        <option value="bleed" ${{eff.type==='bleed'?'selected':''}}>Bleed</option>
                        <option value="bonebreak" ${{eff.type==='bonebreak'?'selected':''}}>Bonebreak</option>
                        <option value="defense" ${{eff.type==='defense'?'selected':''}}>Defense</option>
                        <option value="heal" ${{eff.type==='heal'?'selected':''}}>Heal</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:11px;color:var(--text-dim)">Duration/Value</label>
                    <input id="ed-dur-${{idx}}" type="number" value="${{eff.dur || eff.pct ? Math.round((eff.pct||0)*100) : eff.reduction ? Math.round((eff.reduction||0)*100) : 2}}" style="width:100%;padding:6px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px">
                </div>
            </div>
            <div style="display:flex;gap:8px">
                <button onclick="saveEdit(${{idx}})" class="btn btn-primary" style="font-size:12px;padding:4px 12px">Save</button>
                <button onclick="renderAbilities()" class="btn" style="font-size:12px;padding:4px 12px;background:var(--bg3);color:var(--text)">Cancel</button>
            </div>
        `;
    }}

    function saveEdit(idx) {{
        const name = document.getElementById('ed-name-' + idx).value.trim();
        const desc = document.getElementById('ed-desc-' + idx).value.trim();
        const base = parseInt(document.getElementById('ed-base-' + idx).value) || 0;
        const cd = parseInt(document.getElementById('ed-cd-' + idx).value) || 0;
        const effType = document.getElementById('ed-eff-' + idx).value;
        const durVal = parseInt(document.getElementById('ed-dur-' + idx).value) || 0;
        if (!name) {{ alert('Name is required'); return; }}
        dinoAbilities[idx].name = name;
        dinoAbilities[idx].desc = desc || 'an attack';
        dinoAbilities[idx].base = base;
        dinoAbilities[idx].cd = cd;
        if (effType) {{
            const eff = {{type: effType}};
            if (effType === 'bleed') {{ eff.dur = durVal; eff.pct = 0.03; }}
            else if (effType === 'bonebreak') {{ eff.dur = durVal; }}
            else if (effType === 'defense') {{ eff.dur = durVal; eff.reduction = durVal / 100; }}
            else if (effType === 'heal') {{ eff.pct = durVal / 100; }}
            dinoAbilities[idx].effects = [eff];
        }} else {{
            dinoAbilities[idx].effects = [];
        }}
        renderAbilities();
    }}

    function addAbility() {{
        dinoAbilities.push({{
            name: 'New Ability',
            base: 100,
            cd: 0,
            effects: [],
            desc: 'a custom attack'
        }});
        renderAbilities();
        editAbility(dinoAbilities.length - 1);
    }}

    // Render on load
    renderAbilities();

    async function saveProfile() {{
        const btn = document.getElementById('saveProfileBtn');
        btn.disabled = true;
        btn.textContent = 'Saving...';
        try {{
            const r = await fetch('/api/update-dino-profile', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    id: '{dino_id}',
                    lore: document.getElementById('loreInput').value,
                    custom_abilities: dinoAbilities
                }})
            }});
            const data = await r.json();
            if (data.ok) {{
                btn.textContent = 'Saved!';
                setTimeout(() => btn.textContent = 'Save Changes', 2000);
            }} else {{
                alert(data.error);
                btn.textContent = 'Save Changes';
            }}
        }} catch(err) {{
            alert(err);
            btn.textContent = 'Save Changes';
        }}
        btn.disabled = false;
    }}

    async function uploadAvatar(input) {{
        if (!input.files || !input.files[0]) return;
        const file = input.files[0];
        if (file.size > 5 * 1024 * 1024) {{
            alert('Image must be under 5MB');
            return;
        }}
        const reader = new FileReader();
        reader.onload = async function(e) {{
            try {{
                const r = await fetch('/api/upload-dino-avatar', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{
                        id: '{dino_id}',
                        image: e.target.result
                    }})
                }});
                const data = await r.json();
                if (data.ok) {{
                    const img = document.getElementById('avatarImg');
                    img.src = '/assets/dinos/{dino_id}.png?' + Date.now();
                    img.style.display = 'block';
                    const noText = document.getElementById('noAvatarText');
                    if (noText) noText.style.display = 'none';
                }} else {{
                    alert(data.error || 'Upload failed');
                }}
            }} catch(err) {{
                alert('Upload error: ' + err);
            }}
        }};
        reader.readAsDataURL(file);
    }}

    async function resetAvatar() {{
        if (!confirm('Reset avatar to default?')) return;
        try {{
            const r = await fetch('/api/reset-dino-avatar', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{ id: '{dino_id}' }})
            }});
            const data = await r.json();
            if (data.ok) {{
                const img = document.getElementById('avatarImg');
                img.src = '/assets/dinos/defaults/{dino_id}.png?' + Date.now();
                img.style.display = 'block';
                img.onerror = function() {{ this.style.display='none'; document.getElementById('noAvatarText').style.display='block'; }};
                document.getElementById('noAvatarText').style.display = 'none';
            }}
        }} catch(err) {{ alert('Reset error: ' + err); }}
    }}

    function confirmDelete() {{
        const modal = document.getElementById('deleteModal');
        modal.style.display = 'flex';
    }}

    async function executeDelete() {{
        try {{
            await fetch('/api/delete-card', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{ id: '{dino_id}' }})
            }});
            window.location = '/battle';
        }} catch(err) {{ alert('Delete failed: ' + err); }}
    }}

    // Load battle history from server
    fetch('/api/dino-stats/{dino_id}').then(r => r.json()).then(stats => {{
        const el = document.getElementById('battleHistory');
        if (!stats.ok || !stats.data) {{
            el.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:12px">No battles recorded yet. Start battling in Discord with <code>!dinobattle</code>!</div>';
            return;
        }}
        const d = stats.data;
        const t = d.total_battles || 0;
        const wr = t > 0 ? Math.round((d.wins / t) * 100) : 0;

        let html = '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px;margin-bottom:16px">';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--green)">' + (d.wins||0) + '</div><div style="font-size:11px;color:var(--text-dim)">Wins</div></div>';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--red)">' + (d.losses||0) + '</div><div style="font-size:11px;color:var(--text-dim)">Losses</div></div>';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--accent)">' + wr + '%</div><div style="font-size:11px;color:var(--text-dim)">Win Rate</div></div>';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--text-bright)">💀 ' + (d.kills||0) + '</div><div style="font-size:11px;color:var(--text-dim)">Kills</div></div>';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--text-dim)">☠️ ' + (d.deaths||0) + '</div><div style="font-size:11px;color:var(--text-dim)">Deaths</div></div>';
        html += '<div style="text-align:center;padding:10px;background:var(--bg3);border-radius:8px"><div style="font-size:20px;font-weight:800;color:var(--text-dim)">🏃 ' + (d.flees||0) + '</div><div style="font-size:11px;color:var(--text-dim)">Flees</div></div>';
        html += '</div>';

        // Battle log
        const log = d.battle_log || [];
        if (log.length > 0) {{
            html += '<div style="font-weight:700;font-size:13px;color:var(--text-bright);margin-bottom:8px">Recent Battles (' + log.length + ')</div>';
            html += '<div style="max-height:300px;overflow-y:auto">';
            log.slice().reverse().forEach(function(entry) {{
                const isWin = entry.result === 'win';
                const isTie = entry.result === 'tie';
                const color = isWin ? 'var(--green)' : (isTie ? '#f1c40f' : 'var(--red)');
                const icon = isWin ? '✅' : (isTie ? '🤝' : '❌');
                const ts = entry.timestamp ? new Date(entry.timestamp).toLocaleDateString() : '';
                html += '<div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border-bottom:1px solid var(--border);font-size:12px">';
                html += '<span>' + icon + '</span>';
                html += '<span style="color:' + color + ';font-weight:600;text-transform:uppercase">' + entry.result + '</span>';
                html += '<span style="color:var(--text-dim)">vs</span>';
                html += '<span style="color:var(--text-bright);font-weight:600">' + entry.vs + '</span>';
                if (entry.hp_left != null && entry.hp_max) {{
                    html += '<span style="color:var(--text-dim);margin-left:auto">' + Math.max(0,entry.hp_left) + '/' + entry.hp_max + ' HP</span>';
                }}
                if (ts) html += '<span style="color:var(--text-dim);font-size:11px">' + ts + '</span>';
                html += '</div>';
            }});
            html += '</div>';
        }} else {{
            html += '<div style="color:var(--text-dim);font-size:13px">No battle log yet.</div>';
        }}
        el.innerHTML = html;
    }}).catch(() => {{
        document.getElementById('battleHistory').innerHTML = '<div style="color:var(--text-dim)">No battles recorded yet.</div>';
    }});
    </script>
    """

    return web.Response(text=_page(dino['name'] + " Profile", content, "battle"), content_type="text/html")

# ── API: Update Dino Profile ─────────────────────────────────────
@routes.post("/api/update-dino-profile")
async def api_update_dino_profile(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    dino_id = data.get("id")
    if not dino_id:
        return web.json_response({"error": "Missing ID"}, status=400)

    load_dinos = _state_getters.get("load_dinos")
    save_dinos = _state_getters.get("save_dinos")
    if not load_dinos or not save_dinos:
        return web.json_response({"error": "Bot hooks missing"}, status=500)

    all_dinos = load_dinos()
    for d in all_dinos:
        if d['id'] == dino_id:
            if 'lore' in data:
                d['lore'] = data['lore']
            if 'custom_abilities' in data:
                d['custom_abilities'] = data['custom_abilities']
            save_dinos(all_dinos)
            await push_log(f"\ud83e\udd96 Dashboard: Updated profile for {d['name']}")
            return web.json_response({"ok": True})

    return web.json_response({"error": "Dino not found"}, status=404)

# ── Settings Page ────────────────────────────────────────────────
@routes.get("/settings")
async def settings_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    g = _state_getters
    max_attending = g.get("max_attending", lambda: 10)()
    noshow_thresh = g.get("noshow_threshold", lambda: 3)()
    grace = g.get("checkin_grace", lambda: 30)()
    session_days = g.get("session_days", lambda: [])()
    admin_roles = g.get("admin_role_names", lambda: [])()
    beta_roles = g.get("beta_role_names", lambda: [])()
    archive_ch = g.get("archive_channel_id", lambda: "")()
    schedule_ch = g.get("schedule_channel_id", lambda: "")()
    status_ch = g.get("status_channel_id", lambda: None)()
    battle_ch = g.get("battle_channel_id", lambda: None)()
    start_msg = g.get("status_start_msg", lambda: "")()
    stop_msg = g.get("status_stop_msg", lambda: "")()

    import html as html_mod
    start_msg_safe = html_mod.escape(start_msg or "", quote=True)
    stop_msg_safe = html_mod.escape(stop_msg or "", quote=True)
    admin_roles_json = json.dumps(admin_roles or [])
    beta_roles_json = json.dumps(beta_roles or [])
    cur_session_type = g.get("session_type", lambda: "hunt")()
    nest_parent_ids = g.get("nest_parent_ids", lambda: [])()
    nest_baby_ids = g.get("nest_baby_ids", lambda: [])()
    nest_protector_ids = g.get("nest_protector_ids", lambda: [])()
    nest_parent_ids_json = json.dumps(nest_parent_ids or [])
    nest_baby_ids_json = json.dumps(nest_baby_ids or [])
    nest_protector_ids_json = json.dumps(nest_protector_ids or [])

    days_html = ""
    for d in session_days:
        h = d.get("hour", 0)
        days_html += f'<li>{d.get("name", "?")} at {h:02d}:00 (post {d.get("post_hours_before", 0)}h before)</li>'
    if not days_html:
        days_html = '<li style="color:var(--text-dim)">No days configured</li>'

    content = f"""
    <div class="container">
        <h2 class="page-title">⚙️ Settings</h2>
        <div class="grid grid-2">
            <div class="card">
                <div class="card-header">Session Settings</div>
                <div class="setting-row">
                    <div><div class="setting-label">Max Attending</div><div class="setting-desc">Maximum players in a session</div></div>
                    <input class="setting-input" type="number" id="maxAttending" value="{max_attending}" min="1" max="50">
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">No-Show Threshold</div><div class="setting-desc">Auto-standby after this many no-shows</div></div>
                    <input class="setting-input" type="number" id="noshowThreshold" value="{noshow_thresh}" min="1" max="20">
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">Check-in Grace (min)</div><div class="setting-desc">Minutes to check in after session starts</div></div>
                    <input class="setting-input" type="number" id="graceMinutes" value="{grace}" min="5" max="120">
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">🦴 Session Type</div><div class="setting-desc">Activity type changes the embed look &amp; feel</div></div>
                    <select class="setting-input" id="sessionType" style="text-align:left">
                        <option value="hunt"      {"selected" if cur_session_type == "hunt" else ""}>🦴 Group Hunt</option>
                        <option value="nesting"   {"selected" if cur_session_type == "nesting" else ""}>🥚 Nesting Night</option>
                        <option value="growth"    {"selected" if cur_session_type == "growth" else ""}>🌱 Growth Session</option>
                        <option value="pvp"       {"selected" if cur_session_type == "pvp" else ""}>⚔️ PvP Night</option>
                        <option value="migration" {"selected" if cur_session_type == "migration" else ""}>🏃 Migration Run</option>
                    </select>
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveSettings()">Save Session Settings</button>
                </div>
            </div>
            <div class="card">
                <div class="card-header">Discord Channels</div>
                <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:8px">
                    <div><div class="setting-label">Schedule Channel</div><div class="setting-desc">Where session sign-up posts appear (read-only)</div></div>
                    <input class="setting-input" type="text" value="{schedule_ch}" style="width:100%;font-size:12px;text-align:left" readonly>
                </div>
                <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:8px">
                    <div><div class="setting-label">Archive Channel</div><div class="setting-desc">Channel for session attendance archives</div></div>
                    <div style="display:flex;gap:8px;width:100%">
                        <select id="archiveGuild" class="setting-input" style="width:50%;text-align:left" onchange="filterChannels('archiveGuild','archiveCh')"><option>Loading...</option></select>
                        <select id="archiveCh" class="setting-input" style="width:50%;text-align:left"><option>Loading...</option></select>
                    </div>
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveChannels()">Save Channels</button>
                </div>
            </div>
        </div>
        <div class="card" style="margin-top:16px">
            <div class="card-header">🥚 Nesting Night</div>
            <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px">Configure parents and babies for the nesting session. These changes apply immediately to the active session.</p>
            <div class="grid grid-2" style="gap:16px">
                <div>
                    <div class="setting-label">Parent(s)</div>
                    <div class="setting-desc">Primary nest owners (usually 1 or 2)</div>
                    <div style="display:flex;gap:6px;margin-top:8px">
                        <select class="setting-input" id="nestParentSelect" style="flex:1;text-align:left"><option value="">-- Select Member --</option></select>
                        <button class="btn btn-secondary" style="padding:0 12px" onclick="addNestingUser('parent')">Add</button>
                    </div>
                    <div id="nestParentsContainer" class="role-list" style="margin-top:8px;min-height:40px;align-content:flex-start"></div>
                </div>
                <div>
                    <div class="setting-label">Babies</div>
                    <div class="setting-desc">Users slotted to be nested</div>
                    <div style="display:flex;gap:6px;margin-top:8px">
                        <select class="setting-input" id="nestBabySelect" style="flex:1;text-align:left"><option value="">-- Select Member --</option></select>
                        <button class="btn btn-secondary" style="padding:0 12px" onclick="addNestingUser('baby')">Add</button>
                    </div>
                    <div id="nestBabiesContainer" class="role-list" style="margin-top:8px;min-height:40px;align-content:flex-start"></div>
                </div>
                <div>
                    <div class="setting-label">Protectors</div>
                    <div class="setting-desc">Users defending the nest</div>
                    <div style="display:flex;gap:6px;margin-top:8px">
                        <select class="setting-input" id="nestProtectorSelect" style="flex:1;text-align:left"><option value="">-- Select Member --</option></select>
                        <button class="btn btn-secondary" style="padding:0 12px" onclick="addNestingUser('protector')">Add</button>
                    </div>
                    <div id="nestProtectorsContainer" class="role-list" style="margin-top:8px;min-height:40px;align-content:flex-start"></div>
                </div>
            </div>
            <div style="margin-top:16px;text-align:right">
                <button class="btn btn-primary" onclick="saveNesting()">Save Nesting Role Updates</button>
            </div>
        </div>
        <div class="grid grid-2" style="margin-top:16px">
            <div class="card">
                <div class="card-header">Role Configuration</div>
                <div style="margin-bottom:12px">
                    <div class="setting-label">Admin Roles</div>
                    <div class="setting-desc">Roles with full admin access to the dashboard and bot</div>
                    <div id="adminRolesContainer" class="role-list" style="margin-top:8px">
                        <span style="color:var(--text-dim);font-size:13px">Loading roles...</span>
                    </div>
                </div>
                <div style="margin-bottom:12px">
                    <div class="setting-label">Beta Roles</div>
                    <div class="setting-desc">Roles that can schedule sessions</div>
                    <div id="betaRolesContainer" class="role-list" style="margin-top:8px">
                        <span style="color:var(--text-dim);font-size:13px">Loading roles...</span>
                    </div>
                </div>
                <div style="text-align:right">
                    <button class="btn btn-primary" onclick="saveRoles()">Save Roles</button>
                </div>
            </div>
            <div class="card">
                <div class="card-header">Recurring Session Days</div>
                <ul class="user-list">{days_html}</ul>
                <div style="font-size:12px;color:var(--text-dim);margin-top:8px">
                    Manage recurring days on the <a href="/calendar">Calendar</a> page
                </div>
            </div>
        </div>
        <div style="margin-top:16px">
            <div class="card">
                <div class="card-header">📢 Session Status Notifications</div>
                <p style="font-size:13px;color:var(--text-dim);margin-bottom:16px">
                    Automatic messages posted when sessions start and stop. Pick a template below — the session name is filled in automatically.
                </p>
                <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:8px">
                    <div><div class="setting-label">Status Channel</div><div class="setting-desc">Select guild, then the channel to post session status</div></div>
                    <div style="display:flex;gap:8px;width:100%">
                        <select id="statusGuild" class="setting-input" style="width:50%;text-align:left" onchange="filterChannels('statusGuild','statusCh')"><option>Loading...</option></select>
                        <select id="statusCh" class="setting-input" style="width:50%;text-align:left"><option>Loading...</option></select>
                    </div>
                </div>
                <div class="grid grid-2" style="margin-top:16px;gap:16px">
                    <div>
                        <div class="setting-label" style="margin-bottom:8px">🟢 Session Start Message</div>
                        <select class="setting-input" id="startMsg" style="width:100%;text-align:left;margin-bottom:6px" onchange="previewMsg('startMsg','startPreview')">
                            <option value="🦕 {{name}} is starting! Get ready to stomp!">🦕 Get ready to stomp!</option>
                            <option value="🟢 Session &quot;{{name}}&quot; is now live — jump in!">🟢 Session is now live</option>
                            <option value="⚔️ {{name}} has begun! Time to hunt!">⚔️ Time to hunt!</option>
                            <option value="📢 Session &quot;{{name}}&quot; is starting now">📢 Starting now</option>
                            <option value="🌿 {{name}} — survival begins now!">🌿 Survival begins</option>
                        </select>
                        <div id="startPreview" style="font-size:11px;color:var(--green);padding:4px 8px;background:var(--bg3);border-radius:4px;margin-bottom:8px;min-height:20px"></div>
                        <button class="btn btn-primary btn-sm" style="background:var(--green)" onclick="testStatusMsg('start')">🧪 Test Start</button>
                    </div>
                    <div>
                        <div class="setting-label" style="margin-bottom:8px">🔴 Session Stop Message</div>
                        <select class="setting-input" id="stopMsg" style="width:100%;text-align:left;margin-bottom:6px" onchange="previewMsg('stopMsg','stopPreview')">
                            <option value="🔴 {{name}} has ended. Thanks for playing!">🔴 Thanks for playing!</option>
                            <option value="🦴 Session &quot;{{name}}&quot; is over — great hunt everyone!">🦴 Great hunt everyone!</option>
                            <option value="📊 {{name}} ended — see you next session!">📊 See you next session!</option>
                            <option value="🌙 {{name}} has concluded. Rest up, dinos!">🌙 Rest up, dinos!</option>
                            <option value="🏁 Session &quot;{{name}}&quot; is finished">🏁 Session finished</option>
                        </select>
                        <div id="stopPreview" style="font-size:11px;color:var(--red);padding:4px 8px;background:var(--bg3);border-radius:4px;margin-bottom:8px;min-height:20px"></div>
                        <button class="btn btn-primary btn-sm" style="background:var(--red)" onclick="testStatusMsg('stop')">🧪 Test Stop</button>
                    </div>
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveStatus()">Save Status Settings</button>
                </div>
            </div>
        </div>
        <div style="margin-top:16px">
            <div class="card">
                <div class="card-header">⚔️ Battle Channel</div>
                <p style="font-size:13px;color:var(--text-dim);margin-bottom:16px">
                    Restrict <code>!dinobattle</code> to a specific channel. If set, battles can only be started there.
                </p>
                <div class="setting-row" style="flex-direction:column;align-items:flex-start;gap:8px">
                    <div><div class="setting-label">Battle Channel</div><div class="setting-desc">Select guild, then channel for battles</div></div>
                    <div style="display:flex;gap:8px;width:100%">
                        <select id="battleGuild" class="setting-input" style="width:50%;text-align:left" onchange="filterChannels('battleGuild','battleCh')"><option>Loading...</option></select>
                        <select id="battleCh" class="setting-input" style="width:50%;text-align:left"><option value="">Any Channel (no restriction)</option></select>
                    </div>
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveBattleChannel()">Save Battle Channel</button>
                </div>
            </div>
        </div>
        <div style="margin-top:24px">
            <h3 style="color:var(--text-bright);margin-bottom:12px">📖 Bot Commands Cheat Sheet</h3>
            <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap:16px;">
                <div class="card" style="border-top: 3px solid var(--green)">
                    <div class="card-header" style="color:var(--green)">📖 Everyone Commands</div>
                    <ul class="user-list" style="font-size:13px">
                        <li><strong>!schedule</strong><br><span style="color:var(--text-dim)">Create a Beta Led session. Use `!schedule [type] [hour]`</span></li>
                        <li><strong>!join</strong><br><span style="color:var(--text-dim)">Register attendance (Must be a registered user to join).</span></li>
                        <li><strong>!leave</strong><br><span style="color:var(--text-dim)">Remove your attendance spot.</span></li>
                        <li><strong>!standby</strong><br><span style="color:var(--text-dim)">Move your spot to standby.</span></li>
                        <li><strong>!relieve</strong><br><span style="color:var(--text-dim)">Swap your spot with the person at the top of the standby queue.</span></li>
                        <li><strong>!swap @user</strong><br><span style="color:var(--text-dim)">Swap spots directly with @user.</span></li>
                        <li><strong>!mystats</strong><br><span style="color:var(--text-dim)">View your personal stats.</span></li>
                        <li><strong>!leaderboard [type]</strong><br><span style="color:var(--text-dim)">View server attendance leaderboard.</span></li>
                        <li><strong>!help</strong><br><span style="color:var(--text-dim)">Get a link to the help menu.</span></li>
                        <li><strong>!nest</strong><br><span style="color:var(--text-dim)">Show the current setup of the nesting session (if active).</span></li>
                        <li><strong>!dinobattle</strong><br><span style="color:var(--text-dim)">Start a Pokémon-style random dinosaur card battle!</span></li>
                    </ul>
                </div>
                <div class="card" style="border-top: 3px solid var(--red)">
                    <div class="card-header" style="color:var(--red)">🔒 Admin Commands</div>
                    <ul class="user-list" style="font-size:13px">
                        <li><strong>!setmax &lt;n&gt;</strong><br><span style="color:var(--text-dim)">Set the max number of session attendees.</span></li>
                        <li><strong>!addday &lt;Day&gt; &lt;hour&gt;</strong><br><span style="color:var(--text-dim)">Add a recurring session.</span></li>
                        <li><strong>!removeday &lt;Day&gt;</strong><br><span style="color:var(--text-dim)">Remove a recurring session.</span></li>
                        <li><strong>!kick @user</strong><br><span style="color:var(--text-dim)">Remove user from signups/standby lists.</span></li>
                        <li><strong>!resetstats @user</strong><br><span style="color:var(--text-dim)">Reset a user's attendance stats to zero.</span></li>
                        <li><strong>!setgrace &lt;minutes&gt;</strong><br><span style="color:var(--text-dim)">Set check-in grace period (5-120 min).</span></li>
                        <li><strong>!setnoshow &lt;n&gt;</strong><br><span style="color:var(--text-dim)">Set auto-standby no-show threshold.</span></li>
                        <li><strong>!settings</strong><br><span style="color:var(--text-dim)">Show current bot configuration.</span></li>
                        <li><strong>!settype &lt;type&gt;</strong><br><span style="color:var(--text-dim)">Change session type (hunt, nesting, growth, pvp, migration).</span></li>
                        <li><strong>!parent @user</strong><br><span style="color:var(--text-dim)">Designate a nest parent.</span></li>
                        <li><strong>!baby @user</strong><br><span style="color:var(--text-dim)">Designate a baby.</span></li>
                    </ul>
                </div>
                <div class="card" style="border-top: 3px solid #f1c40f">
                    <div class="card-header" style="color:#f1c40f">🧪 Test Commands</div>
                    <ul class="user-list" style="font-size:13px">
                        <li><strong>!testsession [minutes]</strong><br><span style="color:var(--text-dim)">Create a quick default test session (1 min).</span></li>
                        <li><strong>!testsession &lt;type&gt; [minutes]</strong><br><span style="color:var(--text-dim)">Create a fast test of a specific type.</span></li>
                    </ul>
                    <div style="margin-top:8px;padding:10px;background:var(--bg);border-radius:8px;font-size:12px">
                        <div style="color:var(--text-bright);font-weight:700;margin-bottom:6px">Available Session Types:</div>
                        <div style="display:flex;flex-wrap:wrap;gap:6px">
                            <span style="padding:3px 10px;background:rgba(231,76,60,0.15);border-radius:12px;color:#e74c3c">🦴 hunt</span>
                            <span style="padding:3px 10px;background:rgba(241,196,15,0.15);border-radius:12px;color:#f1c40f">🥚 nesting</span>
                            <span style="padding:3px 10px;background:rgba(46,204,113,0.15);border-radius:12px;color:#2ecc71">🌱 growth</span>
                            <span style="padding:3px 10px;background:rgba(233,30,99,0.15);border-radius:12px;color:#e91e63">⚔️ pvp</span>
                            <span style="padding:3px 10px;background:rgba(52,152,219,0.15);border-radius:12px;color:#3498db">🏃 migration</span>
                        </div>
                        <div style="color:var(--text-dim);margin-top:6px">Example: <code style="background:var(--bg3);padding:2px 6px;border-radius:4px">!testsession nesting 5</code></div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <script>
    let _allGuilds = [];
    const _currentArchive = '{archive_ch}';
    const _currentStatus = '{status_ch or ""}';
    const _currentBattle = '{battle_ch or ""}';

    fetch('/api/channels').then(r => r.json()).then(d => {{
        _allGuilds = d.guilds || [];
        populateGuildDropdown('archiveGuild', 'archiveCh', _currentArchive);
        populateGuildDropdown('statusGuild', 'statusCh', _currentStatus);
        populateGuildDropdown('battleGuild', 'battleCh', _currentBattle);
    }});

    // Role chip selectors
    const _currentAdminRoles = {admin_roles_json};
    const _currentBetaRoles = {beta_roles_json};

    fetch('/api/roles').then(r => r.json()).then(d => {{
        const guilds = d.guilds || [];
        buildRoleChips('adminRolesContainer', guilds, _currentAdminRoles);
        buildRoleChips('betaRolesContainer', guilds, _currentBetaRoles);
    }});

    function buildRoleChips(containerId, guilds, selectedNames) {{
        const container = document.getElementById(containerId);
        container.innerHTML = '';
        if (guilds.length === 0) {{
            container.innerHTML = '<span style="color:var(--text-dim);font-size:13px">No roles found — is the bot in a guild?</span>';
            return;
        }}
        guilds.forEach(g => {{
            if (guilds.length > 1) {{
                const title = document.createElement('div');
                title.className = 'role-section-title';
                title.textContent = g.name;
                container.appendChild(title);
            }}
            g.roles.forEach(role => {{
                const chip = document.createElement('label');
                chip.className = 'role-chip';
                chip.dataset.roleName = role.name;
                const bgColor = role.color || '#99aab5';
                chip.style.background = bgColor + '22';
                chip.style.color = bgColor;
                if (selectedNames.includes(role.name)) chip.classList.add('selected');
                chip.innerHTML = '<span class="role-dot" style="background:' + bgColor + '"></span>' + role.name;
                chip.addEventListener('click', function() {{ chip.classList.toggle('selected'); }});
                container.appendChild(chip);
            }});
        }});
    }}

    // === Nesting Roles Management ===
    let _allMembers = [];
    let _currentNestParents = {nest_parent_ids_json};
    let _currentNestBabies = {nest_baby_ids_json};
    let _currentNestProtectors = {nest_protector_ids_json};

    fetch('/api/members').then(r => r.json()).then(d => {{
        _allMembers = [];
        const guilds = d.guilds || [];
        guilds.forEach(g => {{
            g.members.forEach(m => _allMembers.push(m));
        }});
        
        populateMemberDropdown('nestParentSelect');
        populateMemberDropdown('nestBabySelect');
        populateMemberDropdown('nestProtectorSelect');
        
        renderNestingChips('parent', 'nestParentsContainer', _currentNestParents);
        renderNestingChips('baby', 'nestBabiesContainer', _currentNestBabies);
        renderNestingChips('protector', 'nestProtectorsContainer', _currentNestProtectors);
    }});

    function populateMemberDropdown(selectId) {{
        const sel = document.getElementById(selectId);
        sel.innerHTML = '<option value="">-- Select Member --</option>';
        _allMembers.forEach(m => {{
            const opt = document.createElement('option');
            opt.value = m.id;
            opt.textContent = m.name;
            sel.appendChild(opt);
        }});
    }}

    function renderNestingChips(type, containerId, idList) {{
        const container = document.getElementById(containerId);
        container.innerHTML = '';
        if (idList.length === 0) {{
            const lbl = type === 'parent' ? 'parents' : (type === 'baby' ? 'babies' : 'protectors');
            container.innerHTML = '<span style="color:var(--text-dim);font-size:13px">No ' + lbl + ' configured</span>';
            return;
        }}
        idList.forEach(id => {{
            const member = _allMembers.find(m => m.id === String(id));
            const name = member ? member.name : id;
            
            const chip = document.createElement('div');
            chip.className = 'role-chip selected';
            chip.style.display = 'flex';
            chip.style.alignItems = 'center';
            chip.style.gap = '6px';
            
            const emoji = type === 'parent' ? '🦕' : (type === 'baby' ? '🐣' : '🛡️');
            chip.innerHTML = '<span>' + emoji + ' ' + name + '</span> <span style="font-size:10px;opacity:0.6;background:rgba(0,0,0,0.2);border-radius:50%;width:16px;height:16px;display:flex;align-items:center;justify-content:center;margin-left:4px" onclick="removeNestingUser(\\'' + type + '\\', \\'' + id + '\\', event)">✕</span>';
            
            container.appendChild(chip);
        }});
    }}

    window.addNestingUser = function(type) {{
        const selId = type === 'parent' ? 'nestParentSelect' : (type === 'baby' ? 'nestBabySelect' : 'nestProtectorSelect');
        const containerId = type === 'parent' ? 'nestParentsContainer' : (type === 'baby' ? 'nestBabiesContainer' : 'nestProtectorsContainer');
        const list = type === 'parent' ? _currentNestParents : (type === 'baby' ? _currentNestBabies : _currentNestProtectors);
        
        const sel = document.getElementById(selId);
        const id = sel.value;
        if (!id) return;
        
        if (!list.includes(id)) {{
            list.push(id);
            renderNestingChips(type, containerId, list);
        }}
        sel.value = ''; // reset dropdown
    }};

    window.removeNestingUser = function(type, id, event) {{
        if (event) event.stopPropagation();
        const containerId = type === 'parent' ? 'nestParentsContainer' : (type === 'baby' ? 'nestBabiesContainer' : 'nestProtectorsContainer');
        const list = type === 'parent' ? _currentNestParents : (type === 'baby' ? _currentNestBabies : _currentNestProtectors);
        
        const index = list.indexOf(String(id));
        if (index > -1) {{
            list.splice(index, 1);
            renderNestingChips(type, containerId, list);
        }}
    }};

    window.saveNesting = function() {{
        _post('/api/settings', {{
            nest_parent_ids: _currentNestParents,
            nest_baby_ids: _currentNestBabies,
            nest_protector_ids: _currentNestProtectors
        }}, 'Nesting roles saved! (Applies instantly to active session)');
    }};
    // ================================

    function populateGuildDropdown(guildSelId, chSelId, currentChId) {{
        const gSel = document.getElementById(guildSelId);
        const cSel = document.getElementById(chSelId);
        gSel.innerHTML = '';
        if (_allGuilds.length === 0) {{
            gSel.innerHTML = '<option value="">No guilds</option>';
            cSel.innerHTML = '<option value="">No channels</option>';
            return;
        }}
        let selectedGuildId = _allGuilds[0].id;
        if (currentChId) {{
            for (const g of _allGuilds) {{
                for (const ch of g.channels) {{
                    if (ch.id === String(currentChId)) {{ selectedGuildId = g.id; break; }}
                }}
            }}
        }}
        _allGuilds.forEach(g => {{
            const opt = document.createElement('option');
            opt.value = g.id;
            opt.textContent = g.name;
            if (g.id === selectedGuildId) opt.selected = true;
            gSel.appendChild(opt);
        }});
        filterChannels(guildSelId, chSelId, currentChId);
    }}

    function filterChannels(guildSelId, chSelId, preselect) {{
        const guildId = document.getElementById(guildSelId).value;
        const cSel = document.getElementById(chSelId);
        cSel.innerHTML = '';
        const guild = _allGuilds.find(g => g.id === guildId);
        if (!guild) return;
        if (chSelId === 'statusCh') {{
            const n = document.createElement('option');
            n.value = ''; n.textContent = '— None (disabled) —';
            cSel.appendChild(n);
        }}
        guild.channels.forEach(ch => {{
            const opt = document.createElement('option');
            opt.value = ch.id;
            opt.textContent = ch.name;
            if (preselect && ch.id === String(preselect)) opt.selected = true;
            cSel.appendChild(opt);
        }});
    }}

    function saveSettings() {{
        _post('/api/settings', {{
            max_attending: parseInt(document.getElementById('maxAttending').value),
            checkin_grace: parseInt(document.getElementById('graceMinutes').value),
            noshow_threshold: parseInt(document.getElementById('noshowThreshold').value),
            session_type: document.getElementById('sessionType').value
        }}, 'Session settings saved!');
    }}
    function saveChannels() {{
        _post('/api/settings', {{ archive_channel_id: document.getElementById('archiveCh').value }}, 'Channel settings saved!');
    }}
    function saveRoles() {{
        const adminNames = [];
        document.querySelectorAll('#adminRolesContainer .role-chip.selected').forEach(el => {{
            adminNames.push(el.dataset.roleName);
        }});
        const betaNames = [];
        document.querySelectorAll('#betaRolesContainer .role-chip.selected').forEach(el => {{
            betaNames.push(el.dataset.roleName);
        }});
        _post('/api/settings', {{ admin_role_names: adminNames, beta_role_names: betaNames }}, 'Role settings saved!');
    }}
    function saveBattleChannel() {{
        _post('/api/settings', {{
            battle_channel_id: document.getElementById('battleCh').value
        }}, 'Battle channel saved!');
    }}
    function saveStatus() {{
        _post('/api/settings', {{
            status_channel_id: document.getElementById('statusCh').value,
            status_start_msg: document.getElementById('startMsg').value,
            status_stop_msg: document.getElementById('stopMsg').value
        }}, 'Status settings saved!');
    }}
    function testStatusMsg(type) {{
        const chId = document.getElementById('statusCh').value;
        if (!chId) {{ alert('Select a status channel first'); return; }}
        const msg = type === 'start' ? document.getElementById('startMsg').value : document.getElementById('stopMsg').value;
        fetch('/api/test-status-msg', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ channel_id: chId, type: type, message: msg }})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{ _toast('Test ' + type + ' message sent!'); }}
            else {{ alert(d.error || 'Failed'); }}
        }});
    }}

    // Live preview for message templates
    function previewMsg(selId, previewId) {{
        const val = document.getElementById(selId).value;
        document.getElementById(previewId).textContent = 'Preview: ' + val.replace(/\{{name\}}/g, 'Monday Night Hunt');
    }}

    // Pre-select saved message templates on load
    const _savedStart = '{start_msg_safe}';
    const _savedStop = '{stop_msg_safe}';
    function preselectOption(selId, savedVal) {{
        if (!savedVal) return;
        const sel = document.getElementById(selId);
        for (let i = 0; i < sel.options.length; i++) {{
            if (sel.options[i].value === savedVal) {{
                sel.selectedIndex = i;
                break;
            }}
        }}
    }}
    preselectOption('startMsg', _savedStart);
    preselectOption('stopMsg', _savedStop);
    previewMsg('startMsg', 'startPreview');
    previewMsg('stopMsg', 'stopPreview');
    function _post(url, data, msg) {{
        fetch(url, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(data)
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{ _toast(msg); }}
            else {{ alert(d.error || 'Failed'); }}
        }});
    }}
    function _toast(msg) {{
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 3000);
    }}
    </script>"""

    return web.Response(text=_page("Settings", content, "settings"), content_type="text/html")




# ── Calendar Page ────────────────────────────────────────────────
CALENDAR_CSS = """
<style>
/* ── Google Calendar-Style Week View ───────────────────────── */
.gcal-toolbar { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; flex-wrap:wrap; gap:8px; }
.gcal-toolbar h2 { color:var(--text-bright); font-size:22px; font-weight:600; margin:0; }
.gcal-nav { display:flex; gap:6px; align-items:center; }
.gcal-nav button { background:var(--bg3); border:1px solid var(--border); color:var(--text); padding:7px 16px; border-radius:20px; cursor:pointer; font-size:13px; font-weight:500; transition:all 0.15s; }
.gcal-nav button:hover { background:var(--accent); color:#fff; border-color:var(--accent); }
.gcal-nav .today-btn { background:transparent; border:2px solid var(--accent); color:var(--accent); font-weight:600; }
.gcal-nav .today-btn:hover { background:var(--accent); color:#fff; }
.gcal-tz { font-size:12px; color:var(--text-dim); }

/* Grid layout */
.gcal-container { display:flex; gap:16px; }
.gcal-main { flex:1; min-width:0; }
.gcal-side { width:260px; flex-shrink:0; }

/* Kanban Layout */
.kanban-board { display:flex; gap:12px; overflow-x:auto; padding-bottom:15px; min-height: 600px; scroll-snap-type: x mandatory; scroll-behavior: smooth; }
.kanban-board::-webkit-scrollbar { height: 8px; }
.kanban-board::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
.kanban-col { background:var(--bg); border:1px solid var(--border); border-radius:var(--radius); flex: 1 0 260px; min-width: 260px; display:flex; flex-direction:column; scroll-snap-align: start; }
.kanban-header { padding:12px 10px; border-bottom:1px solid var(--border); font-weight:600; text-align:center; background:var(--bg2); border-radius:var(--radius) var(--radius) 0 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
.kanban-cards { min-height:150px; padding:10px; display:flex; flex-direction:column; gap:10px; flex: 1; }
.kanban-cards.drag-over { background:rgba(88,101,242,0.1); }
.k-card { position:relative; background:var(--bg2); border:1px solid var(--border); border-left:4px solid var(--accent); padding:12px; padding-right:24px; border-radius:6px; cursor:grab; box-shadow: 0 1px 3px rgba(0,0,0,0.2); transition: transform 0.1s; display:flex; flex-direction:column; gap:6px; }
.k-card:hover { border-left-color: var(--green); transform: translateY(-2px); }
.k-card:active { cursor:grabbing; }
.k-title { font-weight: 600; font-size: 14px; color: var(--text-bright); }
.k-time { font-size: 12px; color: var(--text-dim); }
.k-edit-btn { position:absolute; top:8px; right:8px; font-size:12px; background:none; border:none; color:var(--text-dim); cursor:pointer; padding:2px; z-index:5; }
.k-edit-btn:hover { color:var(--text-bright); }
.k-actions { position:absolute; top:6px; right:6px; display:flex; gap:2px; opacity:0; transition:opacity 0.2s; z-index:5; }
.k-card:hover .k-actions { opacity:1; }
.k-act-btn { background:none; border:none; cursor:pointer; font-size:11px; padding:3px 4px; border-radius:4px; transition:background 0.15s; }
.k-act-btn:hover { background:rgba(255,255,255,0.1); }
.k-act-del:hover { background:rgba(240,71,71,0.2); }
.k-subtitle { font-size:11px; color:var(--text-dim); opacity:0.7; font-style:italic; }
.k-add { padding:8px; margin:0 10px 10px; border:1px dashed var(--border); text-align:center; color:var(--text-dim); cursor:pointer; font-size:12px; font-weight:600; border-radius:6px; transition:0.2s; }
.k-add:hover { background:rgba(255,255,255,0.05); color:var(--text-bright); border-color:var(--text-dim); }

/* Event blocks */
.evt-block { position:absolute; left:2px; right:2px; border-radius:4px; padding:4px 6px; font-size:11px; cursor:pointer; z-index:10; overflow:hidden; transition:box-shadow 0.15s; border-left:3px solid; }
.evt-block:hover { box-shadow:0 2px 8px rgba(0,0,0,0.3); z-index:20; }
.evt-block.evt-recurring { background:rgba(88,101,242,0.2); border-color:var(--accent); color:var(--accent); }
.evt-block.evt-active { background:rgba(67,181,129,0.2); border-color:var(--green); color:var(--green); }
.evt-block.evt-ended { background:rgba(240,71,71,0.15); border-color:var(--red); color:var(--red); }
.evt-block .evt-title { font-weight:600; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.evt-block .evt-time { font-size:10px; opacity:0.8; }
.evt-block .evt-attendees { font-size:10px; margin-top:2px; opacity:0.7; }

/* Now line */
.now-line { position:absolute; left:0; right:0; height:2px; background:var(--red); z-index:15; pointer-events:none; }
.now-line::before { content:''; position:absolute; left:-4px; top:-4px; width:10px; height:10px; background:var(--red); border-radius:50%; }

/* Modal */
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.65); z-index:500; align-items:center; justify-content:center; }
.modal-overlay.active { display:flex; }
.modal { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:24px 28px; width:480px; max-width:92vw; max-height:85vh; overflow-y:auto; }
.modal h3 { color:var(--text-bright); margin-bottom:16px; font-size:18px; }
.modal label { display:block; font-size:12px; color:var(--text-dim); margin-bottom:4px; margin-top:14px; text-transform:uppercase; letter-spacing:0.5px; font-weight:600; }
.modal input, .modal select { width:100%; padding:9px 12px; background:var(--bg); border:1px solid var(--border); border-radius:var(--radius); color:var(--text); font-size:14px; }
.modal input:focus, .modal select:focus { border-color:var(--accent); outline:none; box-shadow:0 0 0 2px rgba(88,101,242,0.2); }
.modal-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:20px; }
.modal .btn-secondary { background:var(--bg3); color:var(--text); border:1px solid var(--border); padding:8px 18px; border-radius:var(--radius); cursor:pointer; font-size:13px; }
.modal .btn-secondary:hover { background:var(--border); }
.modal .btn-danger { background:var(--red); color:white; border:none; padding:8px 18px; border-radius:var(--radius); cursor:pointer; font-size:13px; }
.modal .btn-danger:hover { opacity:0.85; }

/* Attendee list in modal */
.attendee-list { margin-top:8px; }
.attendee-item { display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); font-size:13px; }
.attendee-item:last-child { border-bottom:none; }
.attendee-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.attendee-dot.checked { background:var(--green); }
.attendee-dot.pending { background:var(--orange); }

/* Side panel recurring */
.rec-item { display:flex; align-items:center; gap:8px; padding:10px 0; border-bottom:1px solid var(--border); }
.rec-item:last-child { border-bottom:none; }
.rec-item .rec-color { width:10px; height:10px; border-radius:2px; background:var(--accent); flex-shrink:0; }
.rec-item .rec-info { flex:1; }
.rec-item .rec-name { font-size:13px; font-weight:500; color:var(--text-bright); }
.rec-item .rec-detail { font-size:11px; color:var(--text-dim); }

/* Mini month calendar */
.mini-cal { margin-bottom:16px; }
.mini-cal-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
.mini-cal-header span { font-size:13px; font-weight:600; color:var(--text-bright); }
.mini-cal-header button { background:none; border:none; color:var(--text-dim); cursor:pointer; font-size:14px; padding:2px 6px; }
.mini-cal-header button:hover { color:var(--accent); }
.mini-cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:1px; text-align:center; }
.mini-cal-grid .mc-dow { font-size:10px; color:var(--text-dim); padding:2px; font-weight:600; }
.mini-cal-grid .mc-day { font-size:11px; color:var(--text); padding:4px 2px; border-radius:50%; cursor:pointer; }
.mini-cal-grid .mc-day:hover { background:var(--bg3); }
.mini-cal-grid .mc-day.mc-today { background:var(--accent); color:white; font-weight:600; }
.mini-cal-grid .mc-day.mc-other { color:var(--text-dim); opacity:0.4; }
.mini-cal-grid .mc-day.mc-selected { background:rgba(88,101,242,0.3); }
</style>
"""

@routes.get("/calendar")
async def calendar_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    g = _state_getters
    session_days = g.get("session_days", lambda: [])()
    session_days_json = json.dumps(session_days)

    # Current session info
    session_name = g.get("session_name", lambda: "")()
    session_dt_str = g.get("session_dt_str", lambda: "")()
    session_ended = g.get("session_ended", lambda: True)()
    attending_ids = g.get("attending_ids", lambda: [])()
    standby_ids = g.get("standby_ids", lambda: [])()
    checked_in_ids = g.get("checked_in_ids", lambda: set())()

    # Resolve attendee names (CACHED - no more API lag)
    attendees = []
    for uid in attending_ids:
        name = await _resolve_name(uid)
        attendees.append({"id": uid, "name": name, "checked_in": uid in checked_in_ids, "status": "attending"})
    for uid in standby_ids:
        name = await _resolve_name(uid)
        attendees.append({"id": uid, "name": name, "checked_in": False, "status": "standby"})

    current_session = {
        "name": session_name or "",
        "dt": session_dt_str or "",
        "ended": session_ended,
        "attendees": attendees,
    }
    current_session_json = json.dumps(current_session)

    hour_options = ' '.join(f'<option value="{h}">{h:02d}</option>' for h in range(24))
    hour_options_full = ' '.join(f'<option value="{h}">{h:02d}:00</option>' for h in range(24))

    html_part = f"""
    {CALENDAR_CSS}
    <div class="container" style="max-width:1400px">
        <div class="gcal-toolbar">
            <div class="gcal-nav">
                <h2 id="week-title" style="margin-left:0">Kanban Schedule</h2>
            </div>
            <div class="gcal-tz">Drag and drop recurring sessions to move days | Timezone: EST</div>
        </div>
        <div class="gcal-container">
            <div class="gcal-main">
                <div class="kanban-board" id="kanban-board"></div>
            </div>
            <div class="gcal-side">
                <div class="card" style="padding:12px">
                    <div class="mini-cal" id="mini-cal"></div>
                </div>
                <div class="card" style="margin-top:12px">
                    <div class="card-header">&#x1f7e2; Current Session</div>
                    <div id="current-session-panel"></div>
                </div>
                <div class="card" style="margin-top:12px">
                    <div class="card-header">&#x1f501; Recurring Days</div>
                    <div id="rec-days-list"></div>
                    <button class="btn btn-primary" style="width:100%;margin-top:10px" onclick="openAddRecurring()">+ Add Recurring Day</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal-overlay" id="scheduleModal">
        <div class="modal">
            <h3 id="modalTitle">Schedule Session</h3>
            <label>Session Name</label>
            <input type="text" id="modalName" placeholder="e.g. Thursday 20:00 Session">
            <label>Date</label>
            <input type="date" id="modalDate">
            <label>Start Time (24h)</label>
            <div style="display:flex;gap:8px">
                <select id="modalHour" style="width:50%">{hour_options}</select>
                <select id="modalMinute" style="width:50%">
                    <option value="0">:00</option>
                    <option value="15">:15</option>
                    <option value="30">:30</option>
                    <option value="45">:45</option>
                </select>
            </div>
            <div id="modalAttendeesSection" style="display:none">
                <label>Attendees</label>
                <div id="modalAttendeesList" class="attendee-list"></div>
            </div>
            <div id="sendToChannelSection" style="display:none;margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
                <label>Send Update To Channel</label>
                <div style="display:flex;gap:8px;margin-bottom:8px">
                    <select id="channelGuildSelect" style="flex:1" onchange="filterCalChannels()"><option value="">Loading guilds...</option></select>
                    <select id="channelSelect" style="flex:1"><option value="">Loading channels...</option></select>
                </div>
                <label>Message / Note (optional)</label>
                <input type="text" id="channelMessage" placeholder="e.g. Date changed to Thursday">
                <button class="btn btn-primary" style="width:100%;margin-top:8px;background:#43b581" onclick="sendToChannel()">Send to Channel</button>
            </div>
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal('scheduleModal')">Cancel</button>
                <button class="btn btn-primary" id="modalSubmitBtn" onclick="scheduleSession()">Schedule</button>
            </div>
        </div>
    </div>

    <div class="modal-overlay" id="recurringModal">
        <div class="modal">
            <h3>Add Recurring Day</h3>
            <label>Day of Week</label>
            <select id="recWeekday">
                <option value="0">Monday</option><option value="1">Tuesday</option>
                <option value="2">Wednesday</option><option value="3">Thursday</option>
                <option value="4">Friday</option><option value="5">Saturday</option>
                <option value="6">Sunday</option>
            </select>
            <label>Event Type</label>
            <select id="recType">
                <option value="Hunt">Hunt</option>
                <option value="Nesting">Nesting</option>
                <option value="Growth">Growth</option>
                <option value="PvP">PvP</option>
                <option value="Migration">Migration</option>
                <option value="Session">General Session</option>
            </select>
            <label>Session Hour (24h)</label>
            <select id="recHour">{hour_options_full}</select>
            <label>Post Hours Before</label>
            <input type="number" id="recPostBefore" value="12" min="1" max="48">
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal('recurringModal')">Cancel</button>
                <button class="btn btn-primary" onclick="addRecurring()">Add</button>
            </div>
        </div>
    </div>

    <div class="modal-overlay" id="editRecurringModal">
        <div class="modal">
            <h3>Edit Recurring Card</h3>
            <input type="hidden" id="editRecIndex" value="">
            <label>Session Type</label>
            <select id="editRecType">
                <option value="Hunt">Hunt</option>
                <option value="Nesting">Nesting</option>
                <option value="Growth">Growth</option>
                <option value="PvP">PvP</option>
                <option value="Migration">Migration</option>
                <option value="Session">General Session</option>
            </select>
            <label>Session Hour (24h)</label>
            <select id="editRecHour">{hour_options_full}</select>
            <label>Post Hours Before</label>
            <input type="number" id="editRecPostBefore" value="12" min="1" max="48">
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal('editRecurringModal')">Cancel</button>
                <button class="btn btn-danger" onclick="deleteDraggedRecurring()">Delete</button>
                <button class="btn btn-primary" onclick="saveEditRecurring()">Save Changes</button>
            </div>
        </div>
    </div>
    """

    # JavaScript as a regular string (NOT f-string) to avoid backslash issues
    # We inject variables via .replace()
    js_part = """
    <script>
    const WEEKDAYS_SHORT = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
    const WEEKDAYS_FULL = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
    const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
    const TIME_SLOTS = [
        {label:'12 AM', start:0, end:4},
        {label:'4 AM', start:4, end:8},
        {label:'8 AM', start:8, end:12},
        {label:'12 PM', start:12, end:16},
        {label:'4 PM', start:16, end:20},
        {label:'8 PM', start:20, end:24}
    ];

    let sessionDays = __SESSION_DAYS__;
    let currentSession = __CURRENT_SESSION__;
    let weekStart;
    let miniCalMonth, miniCalYear;

    function init() {
        const now = new Date();
        setWeekOf(now);
        miniCalMonth = now.getMonth();
        miniCalYear = now.getFullYear();
        renderAll();
    }

    function setWeekOf(date) {
        const d = new Date(date);
        d.setDate(d.getDate() - d.getDay());
        d.setHours(0,0,0,0);
        weekStart = d;
    }

    function changeWeek(delta) {
        weekStart.setDate(weekStart.getDate() + 7 * delta);
        renderAll();
    }

    function goToday() {
        setWeekOf(new Date());
        miniCalMonth = new Date().getMonth();
        miniCalYear = new Date().getFullYear();
        renderAll();
    }

    function renderAll() {
        renderKanbanBoard();
        renderMiniCal();
        renderCurrentSession();
        renderRecurring();
        updateTitle();
    }

    function updateTitle() {
        const end = new Date(weekStart);
        end.setDate(end.getDate() + 6);
        const t = document.getElementById('week-title');
        if (weekStart.getMonth() === end.getMonth()) {
            t.textContent = MONTHS[weekStart.getMonth()] + ' ' + weekStart.getDate() + ' \\u2013 ' + end.getDate() + ', ' + weekStart.getFullYear();
        } else {
            t.textContent = MONTHS[weekStart.getMonth()].slice(0,3) + ' ' + weekStart.getDate() + ' \\u2013 ' + MONTHS[end.getMonth()].slice(0,3) + ' ' + end.getDate() + ', ' + end.getFullYear();
        }
    }

    function handleCellClick(el) {
        openScheduleAt(el.dataset.date, parseInt(el.dataset.hour), parseInt(el.dataset.dow));
    }

    let draggedSessionIdx = -1;

    function handleDragStart(e, idx) {
        draggedSessionIdx = idx;
        e.dataTransfer.effectAllowed = 'move';
        setTimeout(() => e.target.style.opacity = '0.5', 0);
    }

    function allowDrop(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
    }

    function handleDrop(e, newDow) {
        e.preventDefault();
        if (draggedSessionIdx === -1) return;
        
        sessionDays[draggedSessionIdx].weekday = newDow;
        draggedSessionIdx = -1;
        
        renderKanbanBoard();
        renderRecurring();
        
        // Sync with backend
        fetch('/api/update-recurring-days', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_days: sessionDays })
        }).then(r => r.json()).then(d => {
            if(d.ok) _toast('Kanban Schedule updated!');
            else alert('Failed to sync Kanban: ' + (d.error || 'Unknown error'));
        });
    }

    function renderKanbanBoard() {
        const board = document.getElementById('kanban-board');
        if (!board) return;
        
        // Discord Bot weekdays: 0=Monday, 6=Sunday
        // JS getDay(): 0=Sunday, 1=Monday
        // WEEKDAYS_FULL index matches JS getDay()
        
        let html = '';
        for (let i = 0; i < 7; i++) {
            const jsDow = (i + 1) % 7; 
            const dayName = WEEKDAYS_FULL[jsDow];
            
            html += '<div class="kanban-col" data-dow="' + i + '" ondragover="allowDrop(event)" ondrop="handleDrop(event, ' + i + ')">';
            html += '<div class="kanban-header">' + dayName + '</div>';
            html += '<div class="kanban-cards">';
            
            sessionDays.forEach(function(sd, idx) {
                if (sd.weekday === i) {
                    const evType = sd.type || 'Session';
                    const typeEmoji = {'Hunt':'\\ud83e\\uddb4','Nesting':'\\ud83e\\udd5a','Growth':'\\ud83c\\udf31','PvP':'\\u2694\\ufe0f','Migration':'\\ud83c\\udf0d'};
                    const emoji = typeEmoji[evType] || '\\ud83d\\udcc5';
                    html += '<div class="k-card" draggable="true" ondragstart="handleDragStart(event, ' + idx + ')">';
                    html += '<div class="k-actions">';
                    html += '<button class="k-act-btn" title="Edit" onclick="event.stopPropagation();openEditRecurring(event, '+idx+')">\\u270f\\ufe0f</button>';
                    html += '<button class="k-act-btn" title="Duplicate" onclick="event.stopPropagation();duplicateRecurring('+idx+')">\\ud83d\\udccb</button>';
                    html += '<button class="k-act-btn k-act-del" title="Delete" onclick="event.stopPropagation();deleteRecurring('+idx+')">\\ud83d\\uddd1\\ufe0f</button>';
                    html += '</div>';
                    html += '<div class="k-title">' + emoji + ' ' + evType + '</div>';
                    html += '<div class="k-time">\\u23f0 ' + String(sd.hour).padStart(2,'0') + ':00</div>';
                    if (sd.name && sd.name !== evType) html += '<div class="k-subtitle">' + sd.name + '</div>';
                    html += '</div>';
                }
            });
            
            html += '</div>';
            html += '<div class="k-add" onclick="openAddRecurring(' + i + ')">+ Add Card</div>';
            html += '</div>';
        }
        
        board.innerHTML = html;
    }

    function renderMiniCal() {
        const el = document.getElementById('mini-cal');
        const today = new Date();
        let html = '<div class="mini-cal-header">';
        html += '<button onclick="changeMiniMonth(-1)">&#9664;</button>';
        html += '<span>' + MONTHS[miniCalMonth].slice(0,3) + ' ' + miniCalYear + '</span>';
        html += '<button onclick="changeMiniMonth(1)">&#9654;</button>';
        html += '</div>';
        html += '<div class="mini-cal-grid">';
        ['S','M','T','W','T','F','S'].forEach(function(d) { html += '<div class="mc-dow">' + d + '</div>'; });

        const first = new Date(miniCalYear, miniCalMonth, 1);
        const startDay = first.getDay();
        const daysInMonth = new Date(miniCalYear, miniCalMonth+1, 0).getDate();
        const prevDays = new Date(miniCalYear, miniCalMonth, 0).getDate();

        for (let i = startDay - 1; i >= 0; i--) {
            html += '<div class="mc-day mc-other">' + (prevDays - i) + '</div>';
        }
        for (let d = 1; d <= daysInMonth; d++) {
            const dt = new Date(miniCalYear, miniCalMonth, d);
            let cls = 'mc-day';
            if (dt.toDateString() === today.toDateString()) cls += ' mc-today';
            const ws = new Date(weekStart);
            const we = new Date(weekStart); we.setDate(we.getDate() + 6);
            if (dt >= ws && dt <= we) cls += ' mc-selected';
            html += '<div class="' + cls + '" onclick="jumpToDate(' + miniCalYear + ',' + miniCalMonth + ',' + d + ')">' + d + '</div>';
        }
        const totalCells = startDay + daysInMonth;
        const rem = (7 - totalCells % 7) % 7;
        for (let i = 1; i <= rem; i++) html += '<div class="mc-day mc-other">' + i + '</div>';
        html += '</div>';
        el.innerHTML = html;
    }

    function changeMiniMonth(delta) {
        miniCalMonth += delta;
        if (miniCalMonth > 11) { miniCalMonth = 0; miniCalYear++; }
        if (miniCalMonth < 0) { miniCalMonth = 11; miniCalYear--; }
        renderMiniCal();
    }

    function jumpToDate(y,m,d) {
        setWeekOf(new Date(y,m,d));
        renderAll();
    }

    function renderCurrentSession() {
        const el = document.getElementById('current-session-panel');
        if (!currentSession.name) {
            el.innerHTML = '<div style="padding:12px;color:var(--text-dim);font-size:13px">No active session</div>';
            return;
        }
        let html = '<div style="padding:8px 0">';
        html += '<div style="font-weight:600;color:var(--text-bright);font-size:14px">' + currentSession.name + '</div>';
        if (currentSession.dt) {
            const dt = new Date(currentSession.dt);
            html += '<div style="font-size:12px;color:var(--text-dim);margin-top:4px">' + dt.toLocaleDateString() + ' \\u00b7 ' + String(dt.getHours()).padStart(2,'0') + ':' + String(dt.getMinutes()).padStart(2,'0') + '</div>';
        }
        const status = currentSession.ended ? '<span style="color:var(--red)">\\u25cf Ended</span>' : '<span style="color:var(--green)">\\u25cf Live</span>';
        html += '<div style="font-size:12px;margin-top:4px">' + status + '</div>';

        const attending = currentSession.attendees.filter(function(a) { return a.status === 'attending'; });
        const standby = currentSession.attendees.filter(function(a) { return a.status === 'standby'; });
        if (attending.length > 0) {
            html += '<div style="margin-top:10px;font-size:11px;color:var(--text-dim);text-transform:uppercase;font-weight:600">Attending (' + attending.length + ')</div>';
            attending.forEach(function(a) {
                const dot = a.checked_in ? 'checked' : 'pending';
                const label = a.checked_in ? '\\u2705' : '\\u23f3';
                html += '<div class="attendee-item"><span class="attendee-dot ' + dot + '"></span>' + a.name + ' <span style="margin-left:auto">' + label + '</span></div>';
            });
        }
        if (standby.length > 0) {
            html += '<div style="margin-top:8px;font-size:11px;color:var(--text-dim);text-transform:uppercase;font-weight:600">Standby (' + standby.length + ')</div>';
            standby.forEach(function(a) {
                html += '<div class="attendee-item"><span class="attendee-dot pending"></span>' + a.name + '</div>';
            });
        }
        if (attending.length === 0 && standby.length === 0) {
            html += '<div style="font-size:12px;color:var(--text-dim);margin-top:8px">No attendees yet</div>';
        }
        html += '<div style="margin-top:10px"><button class="btn btn-primary btn-sm" style="width:100%" onclick="openEditSession()">Edit Session</button></div>';
        html += '</div>';
        el.innerHTML = html;
    }

    function renderRecurring() {
        const el = document.getElementById('rec-days-list');
        if (sessionDays.length === 0) {
            el.innerHTML = '<div style="color:var(--text-dim);padding:12px;font-size:13px">No recurring days</div>';
            return;
        }
        let html = '';
        sessionDays.forEach(function(sd, i) {
            const dayName = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][sd.weekday] || sd.name;
            html += '<div class="rec-item">';
            html += '<div class="rec-color"></div>';
            html += '<div class="rec-info"><div class="rec-name">' + (sd.name || dayName) + '</div>';
            html += '<div class="rec-detail">' + String(sd.hour).padStart(2,'0') + ':00 \\u00b7 post ' + sd.post_hours_before + 'h before</div></div>';
            html += '<button class="btn btn-danger btn-sm" onclick="removeRecurring(' + i + ')" style="padding:2px 6px;font-size:11px">\\u2715</button>';
            html += '</div>';
        });
        el.innerHTML = html;
    }

    let editMode = false;

    function openScheduleAt(dateStr, hour, dow) {
        editMode = false;
        document.getElementById('modalTitle').textContent = 'Schedule Session';
        document.getElementById('modalDate').value = dateStr;
        document.getElementById('modalName').value = WEEKDAYS_FULL[dow] + ' ' + String(hour).padStart(2,'0') + ':00 Session';
        document.getElementById('modalHour').value = String(hour);
        document.getElementById('modalMinute').value = '0';
        document.getElementById('modalAttendeesSection').style.display = 'none';
        document.getElementById('modalSubmitBtn').textContent = 'Schedule';
        document.getElementById('scheduleModal').classList.add('active');
    }

    function openEditSession() {
        if (!currentSession.name) return;
        editMode = true;
        document.getElementById('modalTitle').textContent = 'Edit Session';
        document.getElementById('modalName').value = currentSession.name;
        if (currentSession.dt) {
            const dt = new Date(currentSession.dt);
            const dateStr = dt.getFullYear() + '-' + String(dt.getMonth()+1).padStart(2,'0') + '-' + String(dt.getDate()).padStart(2,'0');
            document.getElementById('modalDate').value = dateStr;
            document.getElementById('modalHour').value = String(dt.getHours());
            document.getElementById('modalMinute').value = String(dt.getMinutes());
        }
        const sec = document.getElementById('modalAttendeesSection');
        const list = document.getElementById('modalAttendeesList');
        sec.style.display = 'block';
        let html = '';
        currentSession.attendees.forEach(function(a) {
            const dot = a.checked_in ? 'checked' : 'pending';
            const statusLabel = a.status === 'standby' ? ' (standby)' : (a.checked_in ? ' \\u2705' : '');
            html += '<div class="attendee-item"><span class="attendee-dot ' + dot + '"></span>' + a.name + '<span style="margin-left:auto;font-size:11px;color:var(--text-dim)">' + statusLabel + '</span></div>';
        });
        if (!html) html = '<div style="color:var(--text-dim);font-size:13px">No attendees</div>';
        list.innerHTML = html;
        document.getElementById('modalSubmitBtn').textContent = 'Save Changes';
        document.getElementById('sendToChannelSection').style.display = 'block';
        loadChannels();
        document.getElementById('scheduleModal').classList.add('active');
    }

    let _calGuilds = [];
    function loadChannels() {
        fetch('/api/channels').then(function(r) { return r.json(); }).then(function(d) {
            _calGuilds = d.guilds || [];
            const gSel = document.getElementById('channelGuildSelect');
            gSel.innerHTML = '';
            if (_calGuilds.length === 0) {
                gSel.innerHTML = '<option value="">No guilds</option>';
                document.getElementById('channelSelect').innerHTML = '<option value="">No channels</option>';
                return;
            }
            _calGuilds.forEach(function(g) {
                var opt = document.createElement('option');
                opt.value = g.id;
                opt.textContent = g.name;
                gSel.appendChild(opt);
            });
            filterCalChannels();
        });
    }

    function filterCalChannels() {
        var guildId = document.getElementById('channelGuildSelect').value;
        var cSel = document.getElementById('channelSelect');
        cSel.innerHTML = '';
        var guild = _calGuilds.find(function(g) { return g.id === guildId; });
        if (!guild) return;
        guild.channels.forEach(function(ch) {
            var opt = document.createElement('option');
            opt.value = ch.id;
            opt.textContent = ch.name;
            cSel.appendChild(opt);
        });
    }

    function sendToChannel() {
        const channelId = document.getElementById('channelSelect').value;
        const msg = document.getElementById('channelMessage').value;
        const name = document.getElementById('modalName').value;
        const date = document.getElementById('modalDate').value;
        const hour = parseInt(document.getElementById('modalHour').value);
        const minute = parseInt(document.getElementById('modalMinute').value);

        if (!channelId) { alert('Please select a channel'); return; }
        if (!name) { alert('Please enter a session name'); return; }

        fetch('/api/send-to-channel', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ channel_id: channelId, name: name, date: date, hour: hour, minute: minute, message: msg })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                showToast('Session update sent to channel!');
                document.getElementById('channelMessage').value = '';
            } else { alert(d.error || 'Failed to send'); }
        });
    }

    function closeModal(id) {
        document.getElementById(id).classList.remove('active');
        document.getElementById('sendToChannelSection').style.display = 'none';
    }

    function scheduleSession() {
        const date = document.getElementById('modalDate').value;
        const name = document.getElementById('modalName').value;
        const hour = parseInt(document.getElementById('modalHour').value);
        const minute = parseInt(document.getElementById('modalMinute').value);
        if (!name) { alert('Please enter a session name'); return; }

        fetch('/api/schedule-session', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ date: date, name: name, hour: hour, minute: minute })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                closeModal('scheduleModal');
                showToast((editMode ? 'Session updated' : 'Session scheduled') + ': ' + name);
                setTimeout(function() { location.reload(); }, 1000);
            } else { alert(d.error || 'Failed'); }
        });
    }

    function openAddRecurring() {
        document.getElementById('recWeekday').value = '0';
        document.getElementById('recHour').value = '20';
        document.getElementById('recPostBefore').value = '12';
        document.getElementById('recurringModal').classList.add('active');
    }

    function addRecurring() {
        const weekday = parseInt(document.getElementById('recWeekday').value);
        const hour = parseInt(document.getElementById('recHour').value);
        const post_hours_before = parseInt(document.getElementById('recPostBefore').value);
        const evType = document.getElementById('recType').value;
        const WDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
        const name = evType;

        fetch('/api/session-days', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ weekday: weekday, hour: hour, name: name, post_hours_before: post_hours_before, type: evType })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                sessionDays = d.days;
                closeModal('recurringModal');
                renderAll();
                showToast('Added recurring: ' + evType + ' at ' + String(hour).padStart(2,'0') + ':00');
            } else { alert(d.error || 'Failed'); }
        });
    }

    function duplicateRecurring(idx) {
        const sd = sessionDays[idx];
        fetch('/api/session-days', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ weekday: sd.weekday, hour: sd.hour, name: sd.name || 'Session', post_hours_before: sd.post_hours_before || 12, type: sd.type || 'Session' })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                sessionDays = d.days;
                renderAll();
                showToast('Duplicated event!');
            } else { alert(d.error || 'Failed'); }
        });
    }

    function deleteRecurring(idx) {
        removeRecurring(idx);
    }

    function removeRecurring(index) {
        if (!confirm('Remove this recurring day?')) return;
        fetch('/api/session-days', {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ index: index })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                sessionDays = d.days;
                closeModal('editRecurringModal');
                renderAll();
                showToast('Recurring day removed');
            } else { alert(d.error || 'Failed'); }
        });
    }

    function deleteDraggedRecurring() {
        const idx = document.getElementById('editRecIndex').value;
        if(idx !== "") removeRecurring(parseInt(idx));
    }

    function openEditRecurring(e, index) {
        e.stopPropagation();
        const sd = sessionDays[index];
        document.getElementById('editRecIndex').value = index;
        document.getElementById('editRecType').value = sd.type || 'Session';
        document.getElementById('editRecHour').value = String(sd.hour);
        document.getElementById('editRecPostBefore').value = String(sd.post_hours_before || 12);
        document.getElementById('editRecurringModal').classList.add('active');
    }

    function saveEditRecurring() {
        const idx = parseInt(document.getElementById('editRecIndex').value);
        const evType = document.getElementById('editRecType').value;
        const hour = parseInt(document.getElementById('editRecHour').value);
        const post = parseInt(document.getElementById('editRecPostBefore').value);

        fetch('/api/edit-recurring-day', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ index: idx, name: evType, type: evType, hour: hour, post_hours_before: post })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                sessionDays = d.days;
                closeModal('editRecurringModal');
                renderAll();
                showToast('Card updated successfully!');
            } else { alert(d.error || 'Failed to update'); }
        });
    }

    function showToast(msg) {
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(function() { t.classList.remove('show'); }, 3000);
    }

    init();
    setInterval(function() { renderWeekGrid(); }, 60000);
    </script>
    """.replace('__SESSION_DAYS__', session_days_json).replace('__CURRENT_SESSION__', current_session_json)

    content = html_part + js_part
    # Sanitize surrogate characters that can come from Discord user names/emoji
    page_html = _page("Calendar", content, "calendar")
    page_html = page_html.encode('utf-8', errors='replace').decode('utf-8')
    return web.Response(text=page_html, content_type="text/html")


# ── API Endpoints ────────────────────────────────────────────────
@routes.get("/api/logs")
async def api_logs(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    return web.json_response({"logs": list(log_buffer)})

@routes.get("/api/logs/stream")
async def api_logs_stream(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    q = asyncio.Queue(maxsize=100)
    _sse_queues.append(q)

    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)

    try:
        while True:
            msg = await q.get()
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await resp.write(f"data: [{ts}] {msg}\n\n".encode())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        _sse_queues.remove(q)
    return resp

@routes.post("/api/reset-stats")
async def api_reset_stats(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    uid = data.get("user_id")
    if not uid:
        return web.json_response({"error": "user_id required"}, status=400)

    history = _state_getters.get("attendance_history", lambda: {})()
    if uid in history:
        history[uid] = {"attended": 0, "no_shows": 0, "total_signups": 0, "streak": 0, "best_streak": 0}
        save_fn = _state_getters.get("save_history")
        if save_fn:
            save_fn()

        name = uid
        if bot_ref:
            try:
                u = await bot_ref.fetch_user(int(uid))
                name = u.display_name
            except:
                pass
        await push_log(f"🔧 Admin dashboard: Reset stats for {name} ({uid})")
        return web.json_response({"ok": True, "name": name})
    return web.json_response({"error": "User not found"}, status=404)

@routes.post("/api/settings")
async def api_save_settings(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    update_fn = _state_getters.get("update_settings")
    if update_fn:
        update_fn(data)
        await push_log(f"🔧 Admin dashboard: Settings updated — {data}")
        return web.json_response({"ok": True})
    return web.json_response({"error": "Settings updater not available"}, status=500)

@routes.get("/api/status")
async def api_status(request):
    """Health check endpoint."""
    return web.json_response({"status": "ok", "timestamp": datetime.now().isoformat()})

# ── Calendar API Endpoints ─────────────────────────────────────
@routes.post("/api/schedule-session")
async def api_schedule_session(request):
    """Create a one-off session on a specific date/time."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    date_str = data.get("date")  # YYYY-MM-DD
    name = data.get("name", "Session")
    hour = int(data.get("hour", 20))
    minute = int(data.get("minute", 0))

    if not date_str:
        return web.json_response({"error": "date required"}, status=400)

    create_fn = _state_getters.get("create_schedule")
    if not create_fn or not bot_ref:
        return web.json_response({"error": "Bot not ready"}, status=500)

    try:
        import pytz
        EST = pytz.timezone("US/Eastern")
        dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
        dt = EST.localize(dt)

        # Get the schedule channel
        schedule_ch_id = _state_getters.get("schedule_channel_id", lambda: None)()
        if not schedule_ch_id:
            return web.json_response({"error": "Schedule channel not configured"}, status=500)

        channel = await bot_ref.fetch_channel(int(schedule_ch_id))
        await create_fn(channel, name, session_dt=dt)
        await push_log(f"📅 Dashboard: Scheduled session '{name}' for {date_str} at {hour:02d}:{minute:02d}")
        return web.json_response({"ok": True})
    except Exception as e:
        await push_log(f"❌ Dashboard schedule error: {e}")
        return web.json_response({"error": str(e)}, status=500)

@routes.post("/api/session-days")
async def api_add_session_day(request):
    """Add a recurring session day."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    weekday = int(data.get("weekday", 0))
    hour = int(data.get("hour", 20))
    name = data.get("name", "Session")
    post_hours_before = int(data.get("post_hours_before", 12))

    new_day = {"weekday": weekday, "hour": hour, "name": name, "post_hours_before": post_hours_before}

    session_days = list(_state_getters.get("session_days", lambda: [])())
    session_days.append(new_day)

    update_fn = _state_getters.get("update_settings")
    if update_fn:
        update_fn({"session_days": session_days})
        await push_log(f"🔁 Dashboard: Added recurring day {name} at {hour:02d}:00")
        return web.json_response({"ok": True, "days": session_days})
    return web.json_response({"error": "Update not available"}, status=500)

@routes.post("/api/edit-recurring-day")
async def api_edit_recurring_day(request):
    """Edit an existing recurring session day."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
        
    try:
        data = await request.json()
        idx_str = data.get("index")
        if idx_str is None:
            return web.json_response({"error": "index required"}, status=400)
            
        idx = int(idx_str)
        name = data.get("name", "Session").strip()
        ev_type = data.get("type", name).strip()
        hour = int(data.get("hour", 20))
        post_hours_before = int(data.get("post_hours_before", 12))
        
        session_days = list(_state_getters.get("session_days", lambda: [])())
        if idx < 0 or idx >= len(session_days):
            return web.json_response({"error": "Invalid index"}, status=400)
            
        if name:
            session_days[idx]["name"] = name
        session_days[idx]["type"] = ev_type
        session_days[idx]["hour"] = hour
        session_days[idx]["post_hours_before"] = post_hours_before
        
        update_fn = _state_getters.get("update_settings")
        if update_fn:
            update_fn({"session_days": session_days})
            await push_log(f"🔁 Dashboard: Edited recurring day at index {idx} to '{name}'")
            return web.json_response({"ok": True, "days": session_days})
            
        return web.json_response({"error": "Update function not found"}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.delete("/api/session-days")
async def api_remove_session_day(request):
    """Remove a recurring session day by index."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    index = int(data.get("index", -1))

    session_days = list(_state_getters.get("session_days", lambda: [])())
    if 0 <= index < len(session_days):
        removed = session_days.pop(index)
        update_fn = _state_getters.get("update_settings")
        if update_fn:
            update_fn({"session_days": session_days})
            await push_log(f"🔁 Dashboard: Removed recurring day {removed.get('name', '?')}")
            return web.json_response({"ok": True, "days": session_days})
    return web.json_response({"error": "Invalid index"}, status=400)

@routes.post("/api/update-recurring-days")
async def api_update_recurring_days(request):
    """Update all recurring session days (e.g. from Kanban drag and drop)."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
        
    try:
        data = await request.json()
        new_session_days = data.get("session_days")
        
        if not isinstance(new_session_days, list):
            return web.json_response({"error": "Invalid payload format."}, status=400)
            
        update_fn = _state_getters.get("update_settings")
        if update_fn:
            update_fn({"session_days": new_session_days})
            await push_log(f"🔁 Dashboard: Updated Kanban Calendar Session Days")
            return web.json_response({"ok": True})
        return web.json_response({"error": "Update not available"}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.get("/api/channels")
async def api_channels(request):
    """Return list of text channels grouped by guild for cascading dropdowns."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    channels = []
    guilds = []
    if bot_ref:
        for guild in bot_ref.guilds:
            guild_channels = []
            for ch in guild.text_channels:
                entry = {
                    "id": str(ch.id),
                    "name": f"#{ch.name}",
                    "guild": guild.name,
                    "guild_id": str(guild.id),
                }
                channels.append(entry)
                guild_channels.append(entry)
            guilds.append({
                "id": str(guild.id),
                "name": guild.name,
                "channels": guild_channels,
            })
    return web.json_response({"channels": channels, "guilds": guilds})

@routes.get("/api/roles")
async def api_roles(request):
    """Return list of roles grouped by guild for role selectors."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    guilds = []
    if bot_ref:
        for guild in bot_ref.guilds:
            guild_roles = []
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
                # Skip @everyone and bot-managed roles
                if role.is_default() or role.managed:
                    continue
                guild_roles.append({
                    "name": role.name,
                    "id": str(role.id),
                    "color": f"#{role.color.value:06x}" if role.color.value else None,
                    "position": role.position,
                })
            guilds.append({
                "id": str(guild.id),
                "name": guild.name,
                "roles": guild_roles,
            })
    return web.json_response({"guilds": guilds})

@routes.get("/api/members")
async def api_members(request):
    """Return list of members grouped by guild for user selectors."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    guilds = []
    if bot_ref:
        for guild in bot_ref.guilds:
            guild_members = []
            for member in sorted(guild.members, key=lambda m: m.display_name.lower()):
                if member.bot:
                    continue
                guild_members.append({
                    "name": member.display_name,
                    "id": str(member.id),
                })
            guilds.append({
                "id": str(guild.id),
                "name": guild.name,
                "members": guild_members,
            })
    return web.json_response({"guilds": guilds})

@routes.post("/api/send-to-channel")
async def api_send_to_channel(request):
    """Send session update embed to a Discord channel."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data = await request.json()
        channel_id = data.get("channel_id")
        session_name_val = data.get("name", "")
        date_str = data.get("date", "")
        hour = int(data.get("hour", 0))
        minute = int(data.get("minute", 0))
        message_text = data.get("message", "")

        if not channel_id:
            return web.json_response({"error": "channel_id required"}, status=400)

        channel = await bot_ref.fetch_channel(int(channel_id))

        import discord
        import pytz
        EST = pytz.timezone("US/Eastern")

        # Build the embed
        embed = discord.Embed(
            title=f"📅 Session Update: {session_name_val}",
            color=0x5865F2,  # Discord blurple
        )

        if date_str:
            dt = datetime.strptime(f"{date_str} {hour:02d}:{minute:02d}", "%Y-%m-%d %H:%M")
            dt = EST.localize(dt)
            unix_ts = int(dt.timestamp())
            embed.add_field(name="Date & Time", value=f"<t:{unix_ts}:f> (<t:{unix_ts}:R>)", inline=False)

        # Add current attendee info
        g = _state_getters
        attending_ids = g.get("attending_ids", lambda: [])()
        standby_ids = g.get("standby_ids", lambda: [])()
        checked_in_ids = g.get("checked_in_ids", lambda: set())()

        if attending_ids:
            names = []
            for uid in attending_ids:
                try:
                    u = await bot_ref.fetch_user(uid)
                    check = "✅" if uid in checked_in_ids else "⏳"
                    names.append(f"{check} {u.display_name}")
                except:
                    names.append(f"⏳ User {uid}")
            embed.add_field(name=f"Attending ({len(attending_ids)})", value="\n".join(names), inline=False)

        if standby_ids:
            names = []
            for uid in standby_ids:
                try:
                    u = await bot_ref.fetch_user(uid)
                    names.append(f"🔹 {u.display_name}")
                except:
                    names.append(f"🔹 User {uid}")
            embed.add_field(name=f"Standby ({len(standby_ids)})", value="\n".join(names), inline=False)

        if message_text:
            embed.add_field(name="📝 Note", value=message_text, inline=False)

        embed.set_footer(text="Updated from Dashboard")

        await channel.send(embed=embed)
        await push_log(f"📤 Dashboard: Sent session update to #{channel.name}")
        return web.json_response({"ok": True})
    except Exception as e:
        await push_log(f"❌ Send to channel error: {e}")
        return web.json_response({"error": str(e)}, status=500)

@routes.post("/api/test-status-msg")
async def api_test_status_msg(request):
    """Send a test start/stop message to a channel."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        data = await request.json()
        channel_id = data.get("channel_id")
        msg_type = data.get("type", "start")  # "start" or "stop"
        message = data.get("message", "")

        if not channel_id:
            return web.json_response({"error": "channel_id required"}, status=400)

        import discord
        channel = await bot_ref.fetch_channel(int(channel_id))

        g = _state_getters
        session_name_val = g.get("session_name", lambda: "Session")()

        # Replace template variables
        msg = message.replace("{name}", session_name_val or "Test Session")

        if msg_type == "start":
            embed = discord.Embed(
                title="Session Started 🟢 (TEST)",
                description=msg,
                color=0x43b581
            )
            embed.set_footer(text="⚠️ This is a test message from the dashboard")
        else:
            embed = discord.Embed(
                title="Session Ended 🔴 (TEST)",
                description=msg,
                color=0xe74c3c
            )
            embed.set_footer(text="⚠️ This is a test message from the dashboard")

        await channel.send(embed=embed)
        await push_log(f"🧪 Dashboard: Sent test {msg_type} message to #{channel.name}")
        return web.json_response({"ok": True})
    except Exception as e:
        await push_log(f"❌ Test status message error: {e}")
        return web.json_response({"error": str(e)}, status=500)

@routes.post("/api/upload-global-frame")
async def api_upload_global_frame(request):
    """Handle multipart global card frame uploads (Left/Right)."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    import os
    reader = await request.multipart()
    side = "left"
    frame_data = None
    
    while True:
        field = await reader.next()
        if field is None:
            break
        if field.name == 'side':
            side = (await field.read()).decode('utf-8').strip()
        elif field.name == 'frame':
            val = await field.read()
            if val:
                frame_data = val
                
    if not frame_data or side not in ["left", "right"]:
        return web.json_response({"error": "Missing valid frame or side parameter."}, status=400)
        
    assets_dir = os.path.join(os.path.dirname(__file__), "assets", "dinos", "frames")
    os.makedirs(assets_dir, exist_ok=True)
    frame_path = os.path.join(assets_dir, f"{side}_frame.png")
    
    try:
        with open(frame_path, 'wb') as f:
            f.write(frame_data)
        await push_log(f"🎯 Dashboard: Uploaded Global {side.title()} Frame to {frame_path}")
    except Exception as e:
        return web.json_response({"error": f"Failed to save frame: {e}"}, status=500)
        
    return web.json_response({"ok": True})

@routes.post("/api/upload-card")
async def api_upload_card(request):
    """Handle multipart avatar uploads and new stat generation."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    import os
    reader = await request.multipart()
    
    # Extract fields
    fields = {}
    image_data = None
    
    while True:
        field = await reader.next()
        if field is None:
            break
        if field.name == 'image':
            image_data = await field.read()
        else:
            fields[field.name] = (await field.read()).decode('utf-8')

    dino_id = fields.get('id', '').strip()
    if not dino_id or not image_data:
        return web.json_response({"error": "Missing ID or Image file."}, status=400)

    # Save Image to assets/dinos/id.png
    assets_dir = os.path.join(os.path.dirname(__file__), "assets", "dinos")
    os.makedirs(assets_dir, exist_ok=True)
    img_path = os.path.join(assets_dir, f"{dino_id}.png")
    
    try:
        with open(img_path, 'wb') as f:
            f.write(image_data)
        await push_log(f"🦖 Dashboard: Uploaded new avatar to {img_path}")
    except Exception as e:
        return web.json_response({"error": f"Failed to save image: {e}"}, status=500)

    # Load JSON, construct template, and save
    new_template = {
        "id": dino_id,
        "name": fields.get('name', 'Unknown'),
        "type": fields.get('type', 'carnivore'),
        "lore": fields.get('lore', '').strip(),
        "cw": int(fields.get('cw', 3000)),
        "hp": int(fields.get('hp', 500)),
        "atk": int(fields.get('atk', 50)),
        "armor": float(fields.get('armor', 1.0)),
        "spd": int(fields.get('spd', 500))
    }

    load_dinos = _state_getters.get("load_dinos")
    save_dinos = _state_getters.get("save_dinos")
    
    if load_dinos and save_dinos:
        all_dinos = load_dinos()
        
        # Check if updating existing
        replaced = False
        for i, d in enumerate(all_dinos):
            if d['id'] == dino_id:
                all_dinos[i] = new_template
                replaced = True
                break
                
        if not replaced:
            all_dinos.append(new_template)
            
        save_dinos(all_dinos)
        await push_log(f"🦖 Dashboard: Registered stats for {new_template['name']} ({dino_id})")
        
    return web.json_response({"ok": True})

@routes.post("/api/edit-current-session")
async def api_edit_current_session(request):
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
        
    try:
        data = await request.json()
        new_name = data.get("name", "").strip()
        new_dt_str = data.get("dt", "").strip()
        
        if not new_name:
            return web.json_response({"error": "Session name cannot be empty."}, status=400)
            
        edit_hook = _state_getters.get("edit_current_session")
        if edit_hook:
            await edit_hook(new_name, new_dt_str)
            await push_log(f"📝 Dashboard: Edited active session: {new_name}")
            return web.json_response({"ok": True})
        return web.json_response({"error": "Bot hook missing"}, status=500)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.get("/api/dinos")
async def api_dinos(request):
    """Return all dino profiles as JSON for client-side battle simulation."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    load_dinos = _state_getters.get("load_dinos")
    dinos = load_dinos() if load_dinos else []
    return web.json_response(dinos)

@routes.get("/api/dino-stats/{dino_id}")
async def api_dino_stats(request):
    """Return battle stats for a specific dino."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    dino_id = request.match_info["dino_id"]
    load_stats = _state_getters.get("load_dino_stats")
    if not load_stats:
        return web.json_response({"ok": False, "error": "Stats loader not available"})
    stats = load_stats()
    dino_stats = stats.get(dino_id)
    if not dino_stats:
        return web.json_response({"ok": False})
    return web.json_response({"ok": True, "data": dino_stats})

@routes.post("/api/battle")
async def api_battle(request):
    """Run a full battle simulation between two dinos using the battle engine."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
        attacker_id = data.get("attacker_id")
        defender_id = data.get("defender_id")
        if not attacker_id or not defender_id:
            return web.json_response({"error": "attacker_id and defender_id required"}, status=400)

        load_dinos = _state_getters.get("load_dinos")
        if not load_dinos:
            return web.json_response({"error": "Dino loader not available"}, status=500)
        all_dinos = load_dinos()

        attacker = next((d for d in all_dinos if d["id"] == attacker_id), None)
        defender = next((d for d in all_dinos if d["id"] == defender_id), None)
        if not attacker or not defender:
            return web.json_response({"error": "Dino not found"}, status=404)

        result = battle_engine.simulate_battle(attacker, defender)

        # Flatten round logs for JSON
        flat_rounds = []
        for round_log in result["rounds"]:
            flat_rounds.append("\n".join(round_log))

        return web.json_response({
            "ok": True,
            "winner": result["winner"],
            "winner_name": result["winner_name"],
            "loser_name": result["loser_name"],
            "rounds": flat_rounds,
            "fighter_a": result["fighter_a"],
            "fighter_b": result["fighter_b"],
            "any_fled": result.get("any_fled", False),
            "bleed_kills": result.get("bleed_kills", 0),
            "first_crit_side": result.get("first_crit_side"),
            "total_kos": result.get("total_kos", 0),
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.post("/api/delete-card")
async def api_delete_card(request):
    """Delete a custom card from the roster."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    import os
    data = await request.json()
    dino_id = data.get("id")
    
    if not dino_id:
        return web.json_response({"error": "Missing ID."}, status=400)

    load_dinos = _state_getters.get("load_dinos")
    save_dinos = _state_getters.get("save_dinos")
    
    if load_dinos and save_dinos:
        all_dinos = load_dinos()
        new_dinos = [d for d in all_dinos if d['id'] != dino_id]
        
        if len(new_dinos) == len(all_dinos):
             return web.json_response({"error": "ID not found."}, status=404)
        
        # Don't let them crash the bot by deleting below 2.
        if len(new_dinos) < 2:
            return web.json_response({"error": "Cannot drop below 2 fighters."}, status=400)

        save_dinos(new_dinos)
        
        # Attempt to delete the image file, but don't hard fail if it's missing or locked
        img_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", f"{dino_id}.png")
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except:
                pass
                
        await push_log(f"🦖 Dashboard: Deleted card ID {dino_id}")
        return web.json_response({"ok": True})
        
    return web.json_response({"error": "Bot state error."}, status=500)

@routes.post("/api/upload-dino-avatar")
async def api_upload_dino_avatar(request):
    """Upload an avatar image for a dino profile. Saves to assets/dinos/{id}.png."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    import base64
    data = await request.json()
    dino_id = data.get("id")
    image_data = data.get("image")

    if not dino_id or not image_data:
        return web.json_response({"error": "Missing ID or image."}, status=400)

    # Validate the dino exists
    load_dinos = _state_getters.get("load_dinos")
    if load_dinos:
        all_dinos = load_dinos()
        if not any(d['id'] == dino_id for d in all_dinos):
            return web.json_response({"error": "Dino not found."}, status=404)

    try:
        # Strip data URI prefix if present (e.g., "data:image/png;base64,...")
        if "," in image_data:
            image_data = image_data.split(",", 1)[1]
        raw = base64.b64decode(image_data)

        # Ensure directory exists
        dinos_dir = os.path.join(os.path.dirname(__file__), "assets", "dinos")
        os.makedirs(dinos_dir, exist_ok=True)

        # Save as PNG (convert via Pillow for consistency)
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        # Resize to a reasonable size for cards
        img.thumbnail((512, 512), Image.Resampling.LANCZOS)
        save_path = os.path.join(dinos_dir, f"{dino_id}.png")
        img.save(save_path, format="PNG")

        await push_log(f"🖼️ Dashboard: Uploaded avatar for {dino_id}")
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"error": f"Upload failed: {str(e)}"}, status=500)

@routes.post("/api/reset-dino-avatar")
async def api_reset_dino_avatar(request):
    """Delete custom avatar to revert to default."""
    if not _check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)

    data = await request.json()
    dino_id = data.get("id")
    if not dino_id:
        return web.json_response({"error": "Missing ID"}, status=400)

    import os
    custom_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", f"{dino_id}.png")
    if os.path.exists(custom_path):
        try:
            os.remove(custom_path)
        except:
            pass
    await push_log(f"🖼️ Dashboard: Reset avatar for {dino_id} to default")
    return web.json_response({"ok": True})

# ── Server Lifecycle ─────────────────────────────────────────────
async def start_dashboard(bot):
    """Start the admin dashboard web server. Called from bot.py on_ready()."""
    global bot_ref
    bot_ref = bot

    app = web.Application()
    app.add_routes(routes)

    # Serve static assets (dino avatars, frames, defaults)
    assets_dir = os.path.join(os.path.dirname(__file__), "assets")
    os.makedirs(os.path.join(assets_dir, "dinos", "defaults"), exist_ok=True)
    app.router.add_static("/assets/", assets_dir, follow_symlinks=True)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    await push_log("🌐 Admin dashboard started on port 8080")
    print("🌐 Admin dashboard started on http://0.0.0.0:8080")
    return runner
