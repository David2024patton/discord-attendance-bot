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
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; min-height: 100vh; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* Nav */
.nav { background: var(--bg2); border-bottom: 1px solid var(--border); padding: 0 24px; display: flex; align-items: center; height: 56px; gap: 32px; position: sticky; top: 0; z-index: 100; }
.nav-brand { font-size: 18px; font-weight: 700; color: var(--text-bright); display: flex; align-items: center; gap: 8px; }
.nav-brand span { color: var(--accent); }
.nav-links { display: flex; gap: 8px; flex: 1; }
.nav-links a { padding: 8px 16px; border-radius: var(--radius); color: var(--text-dim); font-weight: 500; font-size: 14px; transition: all 0.15s; }
.nav-links a:hover, .nav-links a.active { background: var(--bg3); color: var(--text-bright); text-decoration: none; }
.nav-right { font-size: 13px; color: var(--text-dim); }

/* Layout */
.container { max-width: 1200px; margin: 0 auto; padding: 24px; }
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
.login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
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
    .grid-3, .grid-2 { grid-template-columns: 1fr; }
    .nav { padding: 0 12px; gap: 12px; }
    .container { padding: 12px; }
}
"""

# ── HTML Templates ───────────────────────────────────────────────
def _nav(active="home"):
    def cls(page):
        return ' class="active"' if page == active else ''
    return f"""
    <nav class="nav">
        <div class="nav-brand">🎯 <span>Attendance</span> Admin</div>
        <div class="nav-links">
            <a href="/"{cls("home")}>Dashboard</a>
            <a href="/users"{cls("users")}>Users</a>
            <a href="/logs"{cls("logs")}>Logs</a>
            <a href="/settings"{cls("settings")}>Settings</a>
        </div>
        <div class="nav-right">
            <a href="/logout" style="color:var(--text-dim)">Logout</a>
        </div>
    </nav>"""

def _page(title, content, active="home"):
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Attendance Admin</title>
<style>{CSS}</style>
</head><body>
{_nav(active)}
{content}
<div class="toast" id="toast"></div>
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
    noshow_thresh = g.get("noshow_threshold", lambda: 5)()
    grace = g.get("checkin_grace", lambda: 30)()
    session_days = g.get("session_days", lambda: [])()
    admin_roles = g.get("admin_role_names", lambda: [])()
    beta_roles = g.get("beta_role_names", lambda: [])()

    days_html = ""
    for d in session_days:
        days_html += f'<li>{d.get("name", "?")} at {d.get("hour", 0)}:00 (post {d.get("post_hours_before", 0)}h before)</li>'
    if not days_html:
        days_html = '<li style="color:var(--text-dim)">No days configured</li>'

    content = f"""
    <div class="container">
        <div class="grid grid-2">
            <div class="card">
                <div class="card-header">Session Settings</div>
                <div class="setting-row">
                    <div><div class="setting-label">Max Attending</div><div class="setting-desc">Maximum players in a session</div></div>
                    <input class="setting-input" type="number" id="maxAttending" value="{max_attending}" min="1" max="50">
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">No-Show Rate Threshold</div><div class="setting-desc">Auto-standby at 60%+ no-show rate (min 3 signups)</div></div>
                    <span style="color:var(--text-dim)">60%</span>
                </div>
                <div class="setting-row">
                    <div><div class="setting-label">Check-in Grace (min)</div><div class="setting-desc">Minutes to check in after session starts</div></div>
                    <input class="setting-input" type="number" id="graceMinutes" value="{grace}" min="5" max="120">
                </div>
                <div style="margin-top:16px;text-align:right">
                    <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
                </div>
            </div>
            <div class="card">
                <div class="card-header">Session Days</div>
                <ul class="user-list">{days_html}</ul>
                <div class="card-header" style="margin-top:16px">Roles</div>
                <div style="font-size:13px;margin-bottom:6px"><strong>Admin:</strong> {', '.join(admin_roles) or 'None'}</div>
                <div style="font-size:13px"><strong>Beta:</strong> {', '.join(beta_roles) or 'None'}</div>
            </div>
        </div>
    </div>
    <script>
    function saveSettings() {{
        const data = {{
            max_attending: parseInt(document.getElementById('maxAttending').value),
            checkin_grace: parseInt(document.getElementById('graceMinutes').value)
        }};
        fetch('/api/settings', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(data)
        }}).then(r => r.json()).then(d => {{
            if (d.ok) {{
                const t = document.getElementById('toast');
                t.textContent = 'Settings saved!';
                t.classList.add('show');
                setTimeout(() => t.classList.remove('show'), 3000);
            }} else {{ alert(d.error || 'Failed'); }}
        }});
    }}
    </script>"""

    return web.Response(text=_page("Settings", content, "settings"), content_type="text/html")

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
