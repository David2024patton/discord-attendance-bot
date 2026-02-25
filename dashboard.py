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
from datetime import datetime
from aiohttp import web

# ── Shared state (injected by bot.py) ────────────────────────────
bot_ref = None           # reference to the discord bot
log_buffer = deque(maxlen=500)   # ring buffer for log lines
_state_getters = {}      # functions to get bot state

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Wildcats@4113")
SESSION_TOKENS = {}      # token -> expiry
TOKEN_TTL = 86400        # 24h

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
body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; min-height: 100vh; display: flex; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Sidebar */
.sidebar { width: var(--sidebar-w); min-height: 100vh; background: var(--bg2); border-right: 1px solid var(--border); display: flex; flex-direction: column; position: fixed; top: 0; left: 0; z-index: 200; transition: width 0.25s ease; overflow: hidden; }
.sidebar.collapsed { width: var(--sidebar-collapsed); }
.sidebar-header { padding: 16px; display: flex; align-items: center; gap: 10px; border-bottom: 1px solid var(--border); min-height: 56px; }
.sidebar-brand { font-size: 17px; font-weight: 700; color: var(--text-bright); white-space: nowrap; overflow: hidden; }
.sidebar-brand span { color: var(--accent); }
.sidebar.collapsed .sidebar-brand { display: none; }
.toggle-btn { background: none; border: none; color: var(--text-dim); cursor: pointer; font-size: 20px; padding: 4px 6px; border-radius: var(--radius); transition: all 0.15s; flex-shrink: 0; }
.toggle-btn:hover { background: var(--bg3); color: var(--text-bright); }
.sidebar-nav { flex: 1; padding: 8px; display: flex; flex-direction: column; gap: 2px; }
.sidebar-nav a { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: var(--radius); color: var(--text-dim); font-weight: 500; font-size: 14px; transition: all 0.15s; white-space: nowrap; overflow: hidden; text-decoration: none; }
.sidebar-nav a:hover { background: var(--bg3); color: var(--text-bright); text-decoration: none; }
.sidebar-nav a.active { background: rgba(88,101,242,0.15); color: var(--accent); }
.sidebar-nav a .icon { font-size: 18px; min-width: 24px; text-align: center; flex-shrink: 0; }
.sidebar-nav a .label { overflow: hidden; }
.sidebar.collapsed .sidebar-nav a .label { display: none; }
.sidebar-footer { padding: 12px; border-top: 1px solid var(--border); }
.sidebar-footer a { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-radius: var(--radius); color: var(--text-dim); font-size: 13px; transition: all 0.15s; text-decoration: none; }
.sidebar-footer a:hover { background: var(--bg3); color: var(--red); text-decoration: none; }
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
.card { background: var(--bg2); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; }
.card-header { font-size: 13px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; letter-spacing: 0.5px; margin-bottom: 12px; }
.card-value { font-size: 32px; font-weight: 700; color: var(--text-bright); }
.card-value.green { color: var(--green); }
.card-value.red { color: var(--red); }
.card-value.orange { color: var(--orange); }

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
            <button class="toggle-btn" id="toggle-btn" onclick="toggleSidebar()" title="Toggle sidebar">☰</button>
            <div class="sidebar-brand">🎯 <span>Attendance</span></div>
        </div>
        <nav class="sidebar-nav">
            <a href="/" class="{cls('home')}"><span class="icon">📊</span><span class="label">Dashboard</span></a>
            <a href="/calendar" class="{cls('calendar')}"><span class="icon">📅</span><span class="label">Calendar</span></a>
            <a href="/users" class="{cls('users')}"><span class="icon">👥</span><span class="label">Users</span></a>
            <a href="/logs" class="{cls('logs')}"><span class="icon">📋</span><span class="label">Logs</span></a>
            <a href="/settings" class="{cls('settings')}"><span class="icon">⚙️</span><span class="label">Settings</span></a>
        </nav>
        <div class="sidebar-footer">
            <a href="/logout"><span class="icon">🚪</span><span class="label">Logout</span></a>
        </div>
    </aside>"""

def _page(title, content, active="home"):
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Attendance Admin</title>
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
<title>Login — Attendance Admin</title>
<style>{CSS}</style>
</head><body>
<div class="login-wrap">
    <div class="login-box">
        <h2>🎯 Attendance Admin</h2>
        <div class="login-error" id="err">Invalid password</div>
        <form method="POST" action="/login">
            <input type="password" name="password" placeholder="Admin password" autofocus required>
            <button type="submit" class="btn btn-primary">Sign In</button>
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

    # User name resolver
    async def get_name(uid):
        if bot_ref:
            try:
                u = await bot_ref.fetch_user(uid)
                return u.display_name
            except:
                pass
        return str(uid)

    # Build attending list HTML
    attend_html = ""
    for uid in attending:
        name = await get_name(uid)
        check = ' <span style="color:var(--green)">✅</span>' if uid in checked_in else ""
        attend_html += f'<li><span class="dot dot-green"></span>{name}{check}</li>'
    if not attend_html:
        attend_html = '<li style="color:var(--text-dim)">No one yet</li>'

    standby_html = ""
    for uid in standby:
        name = await get_name(uid)
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

        # Name resolution
        name = uid_str
        if bot_ref:
            try:
                u = await bot_ref.fetch_user(int(uid_str))
                name = u.display_name
            except:
                pass

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
    start_msg = g.get("status_start_msg", lambda: "")()
    stop_msg = g.get("status_stop_msg", lambda: "")()

    import html as html_mod
    start_msg_safe = html_mod.escape(start_msg or "", quote=True)
    stop_msg_safe = html_mod.escape(stop_msg or "", quote=True)
    admin_roles_json = json.dumps(admin_roles or [])
    beta_roles_json = json.dumps(beta_roles or [])

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
                    Automatic messages posted when sessions start and stop. Use <code style="background:var(--bg3);padding:2px 6px;border-radius:4px;font-size:12px">{{name}}</code> for the session name.
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
                        <input class="setting-input" type="text" id="startMsg" value="{start_msg_safe}" style="width:100%;text-align:left;margin-bottom:8px">
                        <button class="btn btn-primary btn-sm" style="background:var(--green)" onclick="testStatusMsg('start')">🧪 Test Start</button>
                    </div>
                    <div>
                        <div class="setting-label" style="margin-bottom:8px">🔴 Session Stop Message</div>
                        <input class="setting-input" type="text" id="stopMsg" value="{stop_msg_safe}" style="width:100%;text-align:left;margin-bottom:8px">
                        <button class="btn btn-primary btn-sm" style="background:var(--red)" onclick="testStatusMsg('stop')">🧪 Test Stop</button>
                    </div>
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveStatus()">Save Status Settings</button>
                </div>
            </div>
        </div>
    </div>
    <script>
    let _allGuilds = [];
    const _currentArchive = '{archive_ch}';
    const _currentStatus = '{status_ch or ""}';

    fetch('/api/channels').then(r => r.json()).then(d => {{
        _allGuilds = d.guilds || [];
        populateGuildDropdown('archiveGuild', 'archiveCh', _currentArchive);
        populateGuildDropdown('statusGuild', 'statusCh', _currentStatus);
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
            noshow_threshold: parseInt(document.getElementById('noshowThreshold').value)
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

/* Week grid */
.week-grid { display:grid; grid-template-columns:70px repeat(7,1fr); border:1px solid var(--border); border-radius:var(--radius); overflow:hidden; background:var(--bg); }
.week-header { display:contents; }
.week-header .corner { background:var(--bg2); border-bottom:1px solid var(--border); border-right:1px solid var(--border); padding:8px; }
.day-col-header { background:var(--bg2); border-bottom:1px solid var(--border); border-right:1px solid var(--border); text-align:center; padding:10px 4px; }
.day-col-header:last-child { border-right:none; }
.day-col-header .dow { font-size:11px; text-transform:uppercase; color:var(--text-dim); font-weight:600; letter-spacing:0.5px; }
.day-col-header .day-num { font-size:22px; font-weight:600; color:var(--text-bright); margin-top:2px; line-height:1.2; }
.day-col-header.is-today .day-num { background:var(--accent); color:white; border-radius:50%; width:36px; height:36px; display:inline-flex; align-items:center; justify-content:center; }

/* Time rows */
.time-row { display:contents; }
.time-label { background:var(--bg2); border-right:1px solid var(--border); border-bottom:1px solid var(--border); padding:6px 8px; font-size:11px; color:var(--text-dim); text-align:right; font-weight:500; display:flex; align-items:flex-start; justify-content:flex-end; }
.time-cell { border-right:1px solid var(--border); border-bottom:1px solid var(--border); min-height:80px; position:relative; cursor:pointer; transition:background 0.12s; }
.time-cell:last-child { border-right:none; }
.time-cell:hover { background:rgba(88,101,242,0.06); }

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

    # Resolve attendee names
    attendees = []
    async def get_name(uid):
        if bot_ref:
            try:
                u = await bot_ref.fetch_user(uid)
                return u.display_name
            except:
                pass
        return str(uid)

    for uid in attending_ids:
        name = await get_name(uid)
        attendees.append({"id": uid, "name": name, "checked_in": uid in checked_in_ids, "status": "attending"})
    for uid in standby_ids:
        name = await get_name(uid)
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
                <button class="today-btn" onclick="goToday()">Today</button>
                <button onclick="changeWeek(-1)">&#9664;</button>
                <button onclick="changeWeek(1)">&#9654;</button>
                <h2 id="week-title" style="margin-left:12px"></h2>
            </div>
            <div class="gcal-tz">GMT-05 &middot; EST &middot; 24h</div>
        </div>
        <div class="gcal-container">
            <div class="gcal-main">
                <div class="week-grid" id="week-grid"></div>
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
        renderWeekGrid();
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

    function renderWeekGrid() {
        const grid = document.getElementById('week-grid');
        const today = new Date();
        today.setHours(0,0,0,0);
        const now = new Date();
        let html = '';

        html += '<div class="week-header">';
        html += '<div class="corner"><span class="gcal-tz" style="font-size:10px">GMT-05</span></div>';
        for (let i = 0; i < 7; i++) {
            const d = new Date(weekStart);
            d.setDate(d.getDate() + i);
            const isToday = d.toDateString() === today.toDateString();
            html += '<div class="day-col-header' + (isToday ? ' is-today' : '') + '">';
            html += '<div class="dow">' + WEEKDAYS_SHORT[d.getDay()] + '</div>';
            html += '<div class="day-num">' + d.getDate() + '</div>';
            html += '</div>';
        }
        html += '</div>';

        TIME_SLOTS.forEach(function(slot) {
            html += '<div class="time-row">';
            html += '<div class="time-label">' + String(slot.start).padStart(2,'0') + ':00</div>';

            for (let i = 0; i < 7; i++) {
                const d = new Date(weekStart);
                d.setDate(d.getDate() + i);
                const dateStr = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
                const dow = (d.getDay() + 6) % 7;

                html += '<div class="time-cell" data-date="' + dateStr + '" data-hour="' + slot.start + '" data-dow="' + d.getDay() + '" onclick="handleCellClick(this)">';

                sessionDays.forEach(function(sd) {
                    if (sd.weekday === dow && sd.hour >= slot.start && sd.hour < slot.end) {
                        const topPct = ((sd.hour - slot.start) / 4) * 100;
                        html += '<div class="evt-block evt-recurring" style="top:' + topPct + '%;height:25%" onclick="event.stopPropagation()">';
                        html += '<div class="evt-title">' + (sd.name || 'Session') + '</div>';
                        html += '<div class="evt-time">' + String(sd.hour).padStart(2,'0') + ':00</div>';
                        html += '</div>';
                    }
                });

                if (currentSession.dt) {
                    const sdt = new Date(currentSession.dt);
                    const sDateStr = sdt.getFullYear() + '-' + String(sdt.getMonth()+1).padStart(2,'0') + '-' + String(sdt.getDate()).padStart(2,'0');
                    const sHour = sdt.getHours();
                    if (sDateStr === dateStr && sHour >= slot.start && sHour < slot.end) {
                        const topPct = ((sHour - slot.start) / 4) * 100;
                        const cls = currentSession.ended ? 'evt-ended' : 'evt-active';
                        const attCount = currentSession.attendees.filter(function(a) { return a.status === 'attending'; }).length;
                        html += '<div class="evt-block ' + cls + '" style="top:' + topPct + '%;height:30%" onclick="event.stopPropagation(); openEditSession()">';
                        html += '<div class="evt-title">' + (currentSession.name || 'Session') + '</div>';
                        html += '<div class="evt-time">' + String(sHour).padStart(2,'0') + ':' + String(sdt.getMinutes()).padStart(2,'0') + '</div>';
                        html += '<div class="evt-attendees">\\ud83d\\udc65 ' + attCount + ' attending</div>';
                        html += '</div>';
                    }
                }

                if (d.toDateString() === new Date().toDateString()) {
                    const nowH = now.getHours() + now.getMinutes()/60;
                    if (nowH >= slot.start && nowH < slot.end) {
                        const topPct = ((nowH - slot.start) / 4) * 100;
                        html += '<div class="now-line" style="top:' + topPct + '%"></div>';
                    }
                }

                html += '</div>';
            }
            html += '</div>';
        });

        grid.innerHTML = html;
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
        const WDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
        const name = WDAYS[weekday];

        fetch('/api/session-days', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ weekday: weekday, hour: hour, name: name, post_hours_before: post_hours_before })
        }).then(function(r) { return r.json(); }).then(function(d) {
            if (d.ok) {
                sessionDays = d.days;
                closeModal('recurringModal');
                renderAll();
                showToast('Added recurring: ' + name + ' at ' + String(hour).padStart(2,'0') + ':00');
            } else { alert(d.error || 'Failed'); }
        });
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
                renderAll();
                showToast('Recurring day removed');
            } else { alert(d.error || 'Failed'); }
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
    return web.Response(text=_page("Calendar", content, "calendar"), content_type="text/html")


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

# ── Server Lifecycle ─────────────────────────────────────────────
async def start_dashboard(bot):
    """Start the admin dashboard web server. Called from bot.py on_ready()."""
    global bot_ref
    bot_ref = bot

    app = web.Application()
    app.add_routes(routes)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    await push_log("🌐 Admin dashboard started on port 8080")
    print("🌐 Admin dashboard started on http://0.0.0.0:8080")
    return runner
