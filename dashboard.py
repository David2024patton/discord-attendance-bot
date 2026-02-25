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

@media (max-width: 768px) {
    .sidebar { width: var(--sidebar-collapsed); }
    .sidebar .sidebar-brand { display: none; }
    .sidebar .sidebar-nav a .label { display: none; }
    .sidebar .sidebar-footer a .label { display: none; }
    .main { margin-left: var(--sidebar-collapsed); }
    .grid-3, .grid-2 { grid-template-columns: 1fr; }
    .container { padding: 12px; }
}
"""

# ── Sidebar JS ───────────────────────────────────────────────────
SIDEBAR_JS = """
<script>
const sidebar = document.getElementById('sidebar');
const main = document.getElementById('main');
const toggleBtn = document.getElementById('toggle-btn');
function toggleSidebar() {
    sidebar.classList.toggle('collapsed');
    main.classList.toggle('shifted');
    localStorage.setItem('sidebar-collapsed', sidebar.classList.contains('collapsed'));
}
if (localStorage.getItem('sidebar-collapsed') === 'true') {
    sidebar.classList.add('collapsed');
    main.classList.add('shifted');
}
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
                <div class="setting-row">
                    <div><div class="setting-label">Schedule Channel ID</div><div class="setting-desc">Channel where session posts appear</div></div>
                    <input class="setting-input" type="text" id="scheduleCh" value="{schedule_ch}" style="width:180px;font-size:12px" readonly>
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">Archive Channel ID</div><div class="setting-desc">Channel for session archives</div></div>
                    <input class="setting-input" type="text" id="archiveCh" value="{archive_ch}" style="width:180px;font-size:12px">
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveChannels()">Save Channels</button>
                </div>
            </div>
        </div>
        <div class="grid grid-2" style="margin-top:16px">
            <div class="card">
                <div class="card-header">Role Configuration</div>
                <div class="setting-row">
                    <div><div class="setting-label">Admin Roles</div><div class="setting-desc">Comma-separated role names with admin access</div></div>
                    <input class="setting-input" type="text" id="adminRoles" value="{', '.join(admin_roles)}" style="width:200px;font-size:13px">
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">Beta Roles</div><div class="setting-desc">Comma-separated role names that can schedule</div></div>
                    <input class="setting-input" type="text" id="betaRoles" value="{', '.join(beta_roles)}" style="width:200px;font-size:13px">
                </div>
                <div style="margin-top:16px;text-align:right">
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
    </div>
    <script>
    function saveSettings() {{
        const data = {{
            max_attending: parseInt(document.getElementById('maxAttending').value),
            checkin_grace: parseInt(document.getElementById('graceMinutes').value),
            noshow_threshold: parseInt(document.getElementById('noshowThreshold').value)
        }};
        _post('/api/settings', data, 'Session settings saved!');
    }}
    function saveChannels() {{
        const data = {{
            archive_channel_id: document.getElementById('archiveCh').value
        }};
        _post('/api/settings', data, 'Channel settings saved!');
    }}
    function saveRoles() {{
        const data = {{
            admin_role_names: document.getElementById('adminRoles').value.split(',').map(s=>s.trim()).filter(Boolean),
            beta_role_names: document.getElementById('betaRoles').value.split(',').map(s=>s.trim()).filter(Boolean)
        }};
        _post('/api/settings', data, 'Role settings saved!');
    }}
    function _post(url, data, msg) {{
        fetch(url, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(data)
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                const t = document.getElementById('toast');
                t.textContent = msg;
                t.classList.add('show');
                setTimeout(() => t.classList.remove('show'), 3000);
            }} else {{ alert(d.error || 'Failed'); }}
        }});
    }}
    </script>"""

    return web.Response(text=_page("Settings", content, "settings"), content_type="text/html")

# ── Calendar Page ────────────────────────────────────────────────
CALENDAR_CSS = """
<style>
.cal-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:16px; }
.cal-header h3 { color:var(--text-bright); font-size:20px; }
.cal-nav { display:flex; gap:8px; }
.cal-nav button { background:var(--bg3); border:1px solid var(--border); color:var(--text); padding:6px 14px; border-radius:var(--radius); cursor:pointer; font-size:14px; }
.cal-nav button:hover { background:var(--accent); color:white; }
.cal-grid { display:grid; grid-template-columns:repeat(7,1fr); gap:2px; }
.cal-dow { text-align:center; font-size:11px; text-transform:uppercase; color:var(--text-dim); font-weight:600; padding:8px 0; }
.cal-day { background:var(--bg2); border:1px solid var(--border); border-radius:4px; min-height:90px; padding:6px; cursor:pointer; transition:all 0.15s; position:relative; }
.cal-day:hover { border-color:var(--accent); background:var(--bg3); }
.cal-day.today { border-color:var(--accent); box-shadow:inset 0 0 0 1px var(--accent); }
.cal-day.other-month { opacity:0.3; }
.cal-day .day-num { font-size:13px; font-weight:600; color:var(--text-bright); }
.cal-day .day-events { margin-top:4px; }
.cal-event { font-size:10px; padding:2px 4px; border-radius:3px; margin-bottom:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.cal-event.recurring { background:rgba(88,101,242,0.25); color:var(--accent); }
.cal-event.oneoff { background:rgba(67,181,129,0.25); color:var(--green); }
/* Modal */
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.7); z-index:500; align-items:center; justify-content:center; }
.modal-overlay.active { display:flex; }
.modal { background:var(--bg2); border:1px solid var(--border); border-radius:12px; padding:28px; width:420px; max-width:90vw; }
.modal h3 { color:var(--text-bright); margin-bottom:16px; font-size:18px; }
.modal label { display:block; font-size:13px; color:var(--text-dim); margin-bottom:4px; margin-top:12px; }
.modal input, .modal select { width:100%; padding:8px 12px; background:var(--bg); border:1px solid var(--border); border-radius:var(--radius); color:var(--text); font-size:14px; }
.modal input:focus, .modal select:focus { border-color:var(--accent); outline:none; }
.modal-actions { display:flex; gap:8px; justify-content:flex-end; margin-top:20px; }
.modal .btn-secondary { background:var(--bg3); color:var(--text); border:1px solid var(--border); padding:8px 16px; border-radius:var(--radius); cursor:pointer; font-size:13px; }
.modal .btn-secondary:hover { background:var(--border); }
/* Recurring day manager */
.rec-day { display:flex; align-items:center; gap:8px; padding:8px 0; border-bottom:1px solid var(--border); }
.rec-day:last-child { border-bottom:none; }
.rec-day .rec-name { flex:1; font-weight:500; }
.rec-day .rec-time { color:var(--accent); font-size:13px; }
.rec-day .rec-post { color:var(--text-dim); font-size:12px; }
</style>
"""

@routes.get("/calendar")
async def calendar_page(request):
    if not _check_auth(request):
        raise web.HTTPFound("/login")

    g = _state_getters
    session_days = g.get("session_days", lambda: [])()
    session_days_json = json.dumps(session_days)

    content = f"""
    {CALENDAR_CSS}
    <div class="container">
        <h2 class="page-title">📅 Session Calendar</h2>
        <div class="grid grid-2" style="grid-template-columns:2fr 1fr">
            <div class="card">
                <div class="cal-header">
                    <div class="cal-nav">
                        <button onclick="changeMonth(-1)">◀</button>
                        <button onclick="goToday()">Today</button>
                        <button onclick="changeMonth(1)">▶</button>
                    </div>
                    <h3 id="cal-title"></h3>
                </div>
                <div class="cal-grid" id="cal-grid"></div>
            </div>
            <div class="card">
                <div class="card-header">Recurring Session Days</div>
                <div id="rec-days-list"></div>
                <div style="margin-top:12px">
                    <button class="btn btn-primary" style="width:100%" onclick="openAddRecurring()">➕ Add Recurring Day</button>
                </div>
            </div>
        </div>
    </div>

    <!-- Schedule Session Modal -->
    <div class="modal-overlay" id="scheduleModal">
        <div class="modal">
            <h3>📅 Schedule Session</h3>
            <label>Date</label>
            <input type="text" id="modalDate" readonly>
            <label>Session Name</label>
            <input type="text" id="modalName" placeholder="e.g. Monday 8PM Session">
            <label>Time (24-hour)</label>
            <div style="display:flex;gap:8px">
                <select id="modalHour" style="width:50%">
                    {' '.join(f'<option value="{h}">{h:02d}</option>' for h in range(24))}
                </select>
                <select id="modalMinute" style="width:50%">
                    <option value="0">:00</option>
                    <option value="15">:15</option>
                    <option value="30">:30</option>
                    <option value="45">:45</option>
                </select>
            </div>
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal('scheduleModal')">Cancel</button>
                <button class="btn btn-primary" onclick="scheduleSession()">Schedule</button>
            </div>
        </div>
    </div>

    <!-- Add Recurring Day Modal -->
    <div class="modal-overlay" id="recurringModal">
        <div class="modal">
            <h3>🔁 Add Recurring Day</h3>
            <label>Day of Week</label>
            <select id="recWeekday">
                <option value="0">Monday</option>
                <option value="1">Tuesday</option>
                <option value="2">Wednesday</option>
                <option value="3">Thursday</option>
                <option value="4">Friday</option>
                <option value="5">Saturday</option>
                <option value="6">Sunday</option>
            </select>
            <label>Session Hour (24h)</label>
            <select id="recHour">
                {' '.join(f'<option value="{h}">{h:02d}:00</option>' for h in range(24))}
            </select>
            <label>Post Hours Before</label>
            <input type="number" id="recPostBefore" value="12" min="1" max="48">
            <div class="modal-actions">
                <button class="btn-secondary" onclick="closeModal('recurringModal')">Cancel</button>
                <button class="btn btn-primary" onclick="addRecurring()">Add</button>
            </div>
        </div>
    </div>

    <script>
    const WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
    let sessionDays = {session_days_json};
    let calYear, calMonth;

    function init() {{
        const now = new Date();
        calYear = now.getFullYear();
        calMonth = now.getMonth();
        renderCalendar();
        renderRecurring();
    }}

    function changeMonth(delta) {{
        calMonth += delta;
        if (calMonth > 11) {{ calMonth = 0; calYear++; }}
        if (calMonth < 0) {{ calMonth = 11; calYear--; }}
        renderCalendar();
    }}

    function goToday() {{
        const now = new Date();
        calYear = now.getFullYear();
        calMonth = now.getMonth();
        renderCalendar();
    }}

    function renderCalendar() {{
        const grid = document.getElementById('cal-grid');
        const title = document.getElementById('cal-title');
        const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
        title.textContent = months[calMonth] + ' ' + calYear;

        let html = '';
        // Day of week headers (Mon-Sun)
        ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].forEach(d => {{
            html += '<div class="cal-dow">' + d + '</div>';
        }});

        const firstDay = new Date(calYear, calMonth, 1);
        // JS: 0=Sun → adjust so Mon=0
        let startDow = (firstDay.getDay() + 6) % 7;
        const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
        const today = new Date();

        // Previous month fill
        const prevMonthDays = new Date(calYear, calMonth, 0).getDate();
        for (let i = startDow - 1; i >= 0; i--) {{
            const d = prevMonthDays - i;
            html += '<div class="cal-day other-month"><span class="day-num">' + d + '</span></div>';
        }}

        // Current month days
        for (let d = 1; d <= daysInMonth; d++) {{
            const date = new Date(calYear, calMonth, d);
            const dow = (date.getDay() + 6) % 7; // Mon=0
            const isToday = (date.toDateString() === today.toDateString());
            let cls = 'cal-day' + (isToday ? ' today' : '');

            // Check for recurring sessions on this weekday
            let events = '';
            sessionDays.forEach(sd => {{
                if (sd.weekday === dow) {{
                    events += '<div class="cal-event recurring">' + (sd.name || WEEKDAYS[dow]) + ' ' + String(sd.hour).padStart(2,'0') + ':00</div>';
                }}
            }});

            const dateStr = calYear + '-' + String(calMonth+1).padStart(2,'0') + '-' + String(d).padStart(2,'0');
            html += '<div class="' + cls + '" onclick="openSchedule(\'' + dateStr + '\',' + dow + ')">';
            html += '<span class="day-num">' + d + '</span>';
            if (events) html += '<div class="day-events">' + events + '</div>';
            html += '</div>';
        }}

        // Next month fill
        const totalCells = startDow + daysInMonth;
        const remaining = (7 - (totalCells % 7)) % 7;
        for (let i = 1; i <= remaining; i++) {{
            html += '<div class="cal-day other-month"><span class="day-num">' + i + '</span></div>';
        }}

        grid.innerHTML = html;
    }}

    function renderRecurring() {{
        const el = document.getElementById('rec-days-list');
        if (sessionDays.length === 0) {{
            el.innerHTML = '<div style="color:var(--text-dim);padding:12px;font-size:13px">No recurring days configured</div>';
            return;
        }}
        let html = '';
        sessionDays.forEach((sd, i) => {{
            html += '<div class="rec-day">';
            html += '<span class="rec-name">' + (sd.name || WEEKDAYS[sd.weekday]) + '</span>';
            html += '<span class="rec-time">' + String(sd.hour).padStart(2,'0') + ':00</span>';
            html += '<span class="rec-post">post ' + sd.post_hours_before + 'h before</span>';
            html += '<button class="btn btn-danger btn-sm" onclick="removeRecurring(' + i + ')">✖</button>';
            html += '</div>';
        }});
        el.innerHTML = html;
    }}

    function openSchedule(dateStr, dow) {{
        document.getElementById('modalDate').value = dateStr;
        // Auto-fill name from weekday
        document.getElementById('modalName').value = WEEKDAYS[dow] + ' Session';
        // Default to 20:00
        document.getElementById('modalHour').value = '20';
        document.getElementById('modalMinute').value = '0';
        document.getElementById('scheduleModal').classList.add('active');
    }}

    function closeModal(id) {{
        document.getElementById(id).classList.remove('active');
    }}

    function scheduleSession() {{
        const date = document.getElementById('modalDate').value;
        const name = document.getElementById('modalName').value;
        const hour = parseInt(document.getElementById('modalHour').value);
        const minute = parseInt(document.getElementById('modalMinute').value);
        if (!name) {{ alert('Please enter a session name'); return; }}

        fetch('/api/schedule-session', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ date, name, hour, minute }})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                closeModal('scheduleModal');
                showToast('Session scheduled: ' + name + ' at ' + String(hour).padStart(2,'0') + ':' + String(minute).padStart(2,'0'));
            }} else {{ alert(d.error || 'Failed to schedule'); }}
        }});
    }}

    function openAddRecurring() {{
        document.getElementById('recWeekday').value = '0';
        document.getElementById('recHour').value = '20';
        document.getElementById('recPostBefore').value = '12';
        document.getElementById('recurringModal').classList.add('active');
    }}

    function addRecurring() {{
        const weekday = parseInt(document.getElementById('recWeekday').value);
        const hour = parseInt(document.getElementById('recHour').value);
        const post_hours_before = parseInt(document.getElementById('recPostBefore').value);
        const name = WEEKDAYS[weekday];

        fetch('/api/session-days', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ weekday, hour, name, post_hours_before }})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                sessionDays = d.days;
                closeModal('recurringModal');
                renderCalendar();
                renderRecurring();
                showToast('Added recurring: ' + name + ' at ' + String(hour).padStart(2,'0') + ':00');
            }} else {{ alert(d.error || 'Failed'); }}
        }});
    }}

    function removeRecurring(index) {{
        if (!confirm('Remove this recurring day?')) return;
        fetch('/api/session-days', {{
            method: 'DELETE',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{ index }})
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                sessionDays = d.days;
                renderCalendar();
                renderRecurring();
                showToast('Recurring day removed');
            }} else {{ alert(d.error || 'Failed'); }}
        }});
    }}

    function showToast(msg) {{
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(() => t.classList.remove('show'), 3000);
    }}

    init();
    </script>"""

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
