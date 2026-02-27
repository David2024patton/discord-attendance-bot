import asyncio
import builtins
import io
import json
import os
import sys
from datetime import datetime, timedelta
import pytz
import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont
import dashboard

# ── Intercept print() for dashboard live logs ────────────────────
_original_print = builtins.print
def _captured_print(*args, **kwargs):
    _original_print(*args, **kwargs)
    msg = " ".join(str(a) for a in args)
    dashboard.add_log(msg)
builtins.print = _captured_print

# ----------------------------
# Fix asyncio for Python 3.14+
# ----------------------------
try:
    asyncio.get_running_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# ----------------------------
# Intents
# ----------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ----------------------------
# Config
# ----------------------------
ADMIN_ID = 740522966474948638
OWNER_IDS = {ADMIN_ID, 776628215066132502}  # Bot owner + David — only these can reset stats
DEFAULT_MAX_ATTENDING = 10
DEFAULT_NOSHOW_THRESHOLD = 3  # auto-standby after this many no-shows
DEFAULT_CHECKIN_GRACE = 30  # minutes after session start before auto-relieve

# Session type definitions — emoji, color, label
SESSION_TYPES = {
    'hunt':      {'emoji': '🦴', 'color': 0xe74c3c, 'label': 'Group Hunt'},
    'nesting':   {'emoji': '🥚', 'color': 0xf1c40f, 'label': 'Nesting Night'},
    'growth':    {'emoji': '🌱', 'color': 0x2ecc71, 'label': 'Growth Session'},
    'pvp':       {'emoji': '⚔️', 'color': 0xe91e63, 'label': 'PvP Night'},
    'migration': {'emoji': '🏃', 'color': 0x3498db, 'label': 'Migration Run'},
}

ALLOWED_GUILDS = [1370907857830746194, 1370907957830746194, 1475253514111291594]
SCHEDULE_CHANNEL_ID = 1370911001247223859
DEFAULT_ARCHIVE_CHANNEL_ID = 1448185222842683456  # attendance-tracker channel
EST = pytz.timezone("US/Eastern")

# State file path - use /app/data/ for Docker volume persistence
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

# ----------------------------
# Admin check
# ----------------------------
def is_admin(user):
    """True if user is the hardcoded admin, has a configured admin role, or has server admin perms."""
    if user.id == ADMIN_ID:
        return True
    # Check Discord roles (works for Member objects, not User objects from DMs)
    if hasattr(user, 'roles'):
        for role in user.roles:
            if role.permissions.administrator:
                return True
            if role.name.lower() in [r.lower() for r in admin_role_names]:
                return True
    return False

def has_beta_role(user):
    """True if user has a configured beta scheduling role."""
    if is_admin(user):
        return True  # admins can always schedule
    if hasattr(user, 'roles'):
        for role in user.roles:
            if role.name.lower() in [r.lower() for r in beta_role_names]:
                return True
    return False

async def check_admin(ctx):
    """Returns True if admin. Sends error and returns False if not."""
    if is_admin(ctx.author):
        return True
    await ctx.send("❌ Admin only.", delete_after=5)
    return False

def session_has_started():
    """Returns True if the session datetime has passed and session has NOT ended."""
    if session_ended:
        return False  # session is over, not 'started'
    if not session_dt_str:
        return False
    try:
        session_dt = datetime.fromisoformat(session_dt_str)
        now = datetime.now(session_dt.tzinfo or EST)
        return now >= session_dt
    except:
        return False

def session_is_active():
    """Returns True if the session exists and has NOT ended."""
    return bool(session_dt_str) and not session_ended

# ----------------------------
# State Management
# ----------------------------
def load_state():
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id
    global event_message_id, event_channel_id, session_name, session_dt_str
    global NOSHOW_THRESHOLD, CHECKIN_GRACE_MINUTES
    global last_posted_session, MAX_ATTENDING, session_days, reminder_sent
    global checkin_active, checked_in_ids, checkin_message_id
    global admin_role_names, beta_role_names, archive_channel_id, session_ended
    global status_channel_id, status_start_msg, status_stop_msg
    global battle_channel_id
    global session_type, nest_parent_ids, nest_baby_ids, nest_protector_ids

    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                attending_ids = data.get('attending_ids', [])
                standby_ids = data.get('standby_ids', [])
                not_attending_ids = data.get('not_attending_ids', [])
                pending_offer_id = data.get('pending_offer_id')
                event_message_id = data.get('event_message_id')
                event_channel_id = data.get('event_channel_id')
                session_name = data.get('session_name', 'Session')
                session_dt_str = data.get('session_dt')
                last_posted_session = data.get('last_posted_session')
                MAX_ATTENDING = data.get('max_attending', DEFAULT_MAX_ATTENDING)
                session_days = data.get('session_days', [
                    {"weekday": 0, "hour": 20, "name": "Monday", "post_hours_before": 12},
                    {"weekday": 1, "hour": 20, "name": "Tuesday", "post_hours_before": 20},
                    {"weekday": 2, "hour": 20, "name": "Wednesday", "post_hours_before": 20},
                ])
                reminder_sent = data.get('reminder_sent', False)
                checkin_active = data.get('checkin_active', False)
                checked_in_ids = data.get('checked_in_ids', [])
                checkin_message_id = data.get('checkin_message_id')
                NOSHOW_THRESHOLD = data.get('noshow_threshold', DEFAULT_NOSHOW_THRESHOLD)
                CHECKIN_GRACE_MINUTES = data.get('checkin_grace_minutes', DEFAULT_CHECKIN_GRACE)
                admin_role_names = data.get('admin_role_names', ['Admin'])
                beta_role_names = data.get('beta_role_names', ['Beta', 'Lead Beta'])
                archive_channel_id = data.get('archive_channel_id', DEFAULT_ARCHIVE_CHANNEL_ID)
                session_ended = data.get('session_ended', False)
                status_channel_id = data.get('status_channel_id', None)
                battle_channel_id = data.get('battle_channel_id', None)
                status_start_msg = data.get('status_start_msg', '🟢 **{name}** is now LIVE! Join us!')
                status_stop_msg = data.get('status_stop_msg', '🔴 **{name}** has ended. See you next time!')
                session_type = data.get('session_type', 'hunt')
                nest_parent_ids = data.get('nest_parent_ids', [])
                nest_baby_ids = data.get('nest_baby_ids', [])
                nest_protector_ids = data.get('nest_protector_ids', [])
                print(f"✅ Loaded state from {STATE_FILE}")
                return True
    except Exception as e:
        print(f"❌ Error loading state: {e}")

    # Default empty state
    attending_ids = []
    standby_ids = []
    not_attending_ids = []
    pending_offer_id = None
    event_message_id = None
    event_channel_id = None
    session_name = 'Session'
    session_dt_str = None
    last_posted_session = None
    MAX_ATTENDING = DEFAULT_MAX_ATTENDING
    session_days = [
        {"weekday": 0, "hour": 20, "name": "Monday", "post_hours_before": 12},
        {"weekday": 1, "hour": 20, "name": "Tuesday", "post_hours_before": 20},
        {"weekday": 2, "hour": 20, "name": "Wednesday", "post_hours_before": 20},
    ]
    reminder_sent = False
    checkin_active = False
    checked_in_ids = []
    checkin_message_id = None
    NOSHOW_THRESHOLD = DEFAULT_NOSHOW_THRESHOLD
    CHECKIN_GRACE_MINUTES = DEFAULT_CHECKIN_GRACE
    admin_role_names = ['Admin']
    beta_role_names = ['Beta', 'Lead Beta']
    archive_channel_id = DEFAULT_ARCHIVE_CHANNEL_ID
    session_ended = False
    status_channel_id = None
    battle_channel_id = None
    status_start_msg = '🟢 **{name}** is now LIVE! Join us!'
    status_stop_msg = '🔴 **{name}** has ended. See you next time!'
    session_type = 'hunt'
    nest_parent_ids = []
    nest_baby_ids = []
    nest_protector_ids = []
    return False

def save_state():
    data = {
        'attending_ids': attending_ids,
        'standby_ids': standby_ids,
        'not_attending_ids': not_attending_ids,
        'pending_offer_id': pending_offer_id,
        'event_message_id': event_message_id,
        'event_channel_id': event_channel_id,
        'session_name': session_name,
        'session_dt': session_dt_str,
        'last_posted_session': last_posted_session,
        'max_attending': MAX_ATTENDING,
        'session_days': session_days,
        'reminder_sent': reminder_sent,
        'checkin_active': checkin_active,
        'checked_in_ids': checked_in_ids,
        'checkin_message_id': checkin_message_id,
        'noshow_threshold': NOSHOW_THRESHOLD,
        'checkin_grace_minutes': CHECKIN_GRACE_MINUTES,
        'admin_role_names': admin_role_names,
        'beta_role_names': beta_role_names,
        'archive_channel_id': archive_channel_id,
        'session_ended': session_ended,
        'status_channel_id': status_channel_id,
        'battle_channel_id': battle_channel_id,
        'status_start_msg': status_start_msg,
        'status_stop_msg': status_stop_msg,
        'session_type': session_type,
        'nest_parent_ids': nest_parent_ids,
        'nest_baby_ids': nest_baby_ids,
        'nest_protector_ids': nest_protector_ids,
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving state: {e}")

# ----------------------------
# Attendance History
# ----------------------------
# Format: {user_id_str: {"attended": N, "no_shows": N, "total_signups": N, "streak": N, "best_streak": N}}
attendance_history = {}

def load_history():
    global attendance_history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r') as f:
                attendance_history = json.load(f)
            print(f"✅ Loaded history ({len(attendance_history)} users)")
            return
    except Exception as e:
        print(f"❌ Error loading history: {e}")
    attendance_history = {}

def save_history():
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(attendance_history, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving history: {e}")

def get_user_stats(user_id):
    key = str(user_id)
    if key not in attendance_history:
        attendance_history[key] = {
            "attended": 0, "no_shows": 0, "total_signups": 0,
            "streak": 0, "best_streak": 0,
            "nest_parent_count": 0, "nest_baby_count": 0, "nest_protector_count": 0
        }
    return attendance_history[key]

def record_attendance(user_id):
    stats = get_user_stats(user_id)
    stats["attended"] += 1
    stats["total_signups"] += 1
    stats["streak"] += 1
    if stats["streak"] > stats["best_streak"]:
        stats["best_streak"] = stats["streak"]
    save_history()

def record_no_show(user_id):
    stats = get_user_stats(user_id)
    stats["no_shows"] += 1
    stats["total_signups"] += 1
    stats["streak"] = 0  # reset streak
    save_history()

def is_auto_standby(user_id):
    """Rate-based: auto-standby if no-show rate >= 60% with at least 3 signups"""
    stats = get_user_stats(user_id)
    total = stats["total_signups"]
    if total < 3:
        return False  # Not enough data to judge
    no_show_rate = stats["no_shows"] / total
    return no_show_rate >= 0.6  # 60%+ no-show rate

def streak_badge(user_id):
    stats = get_user_stats(user_id)
    s = stats["streak"]
    if s >= 10:
        return f" 🔥{s}"
    elif s >= 5:
        return f" 🔥{s}"
    elif s >= 3:
        return f" ⚡{s}"
    return ""

# ----------------------------
# Session Archiving
# ----------------------------
async def archive_session():
    """Archives the current session's attendance data to the archive channel."""
    if not session_name or not attending_ids and not standby_ids and not not_attending_ids:
        return  # nothing to archive

    try:
        channel = await bot.fetch_channel(archive_channel_id)
        if not channel:
            print("❌ Archive channel not found")
            return

        now = datetime.now(EST)
        date_str = now.strftime("%m/%d/%Y")

        # Build archive embed
        embed = discord.Embed(
            title=f"📋 Session Archive — {date_str}",
            description=f"**{session_name}**",
            color=0x3498db,
            timestamp=now
        )

        # Session time info
        if session_dt_str:
            try:
                dt = datetime.fromisoformat(session_dt_str)
                unix_ts = int(dt.timestamp())
                embed.add_field(name="🕐 Session Time", value=f"<t:{unix_ts}:f>", inline=True)
            except:
                pass

        # Attending list
        if attending_ids:
            attend_mentions = []
            for uid in attending_ids:
                badge = ""
                if uid in checked_in_ids:
                    badge = " ✅"
                else:
                    badge = " ❌ (no-show)"
                attend_mentions.append(f"<@{uid}>{badge}")
            embed.add_field(
                name=f"👥 Attending ({len(attending_ids)})",
                value="\n".join(attend_mentions) or "None",
                inline=False
            )
        else:
            embed.add_field(name="👥 Attending (0)", value="None", inline=False)

        # Standby
        if standby_ids:
            standby_mentions = [f"<@{uid}> ❓" for uid in standby_ids]
            embed.add_field(
                name=f"⏳ Standby ({len(standby_ids)})",
                value="\n".join(standby_mentions),
                inline=False
            )

        # Not Attending
        if not_attending_ids:
            na_mentions = [f"<@{uid}>" for uid in not_attending_ids]
            embed.add_field(
                name=f"❌ Not Attending ({len(not_attending_ids)})",
                value="\n".join(na_mentions),
                inline=False
            )

        # Leaderboard section — top 5 by attendance rate
        if attendance_history:
            leaderboard_lines = []
            entries = []
            for uid_str, data in attendance_history.items():
                total = data.get("total_signups", 0)
                if total == 0:
                    continue
                attended = data.get("attended", 0)
                no_shows = data.get("no_shows", 0)
                streak = data.get("streak", 0)
                rate = (attended / total) * 100
                entries.append((uid_str, attended, total, rate, streak, no_shows))

            entries.sort(key=lambda x: x[3], reverse=True)
            medals = ["🥇", "🥈", "🥉"]
            for i, (uid_str, attended, total, rate, streak, no_shows) in enumerate(entries[:5]):
                medal = medals[i] if i < 3 else f"{i+1}."
                streak_str = f" 🔥{streak}" if streak >= 3 else ""
                noshow_str = f" ⚠️{no_shows}NS" if no_shows > 0 else ""
                leaderboard_lines.append(
                    f"{medal} <@{uid_str}> — {attended}/{total} ({rate:.0f}%){streak_str}{noshow_str}"
                )

            if leaderboard_lines:
                embed.add_field(
                    name="📊 Leaderboard",
                    value="\n".join(leaderboard_lines),
                    inline=False
                )

        embed.set_footer(text="Session Ended")
        await channel.send(embed=embed)
        print(f"📋 Session archived to #{channel.name}")
    except Exception as e:
        print(f"❌ Archive error: {e}")

async def end_session():
    """Ends the current session: archives, posts offline message, disables buttons."""
    global session_ended, countdown_task

    session_ended = True
    save_state()

    # Cancel countdown timer
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
        countdown_task = None

    # Tally Nesting roles (only for attendees who checked in)
    if session_type == 'nesting':
        for uid in attending_ids:
            if uid in checked_in_ids:
                stats = get_user_stats(uid)
                if uid in nest_parent_ids:
                    stats["nest_parent_count"] += 1
                elif uid in nest_baby_ids:
                    stats["nest_baby_count"] += 1
                elif uid in nest_protector_ids:
                    stats["nest_protector_count"] += 1
        save_history()

    # Archive to the attendance tracker channel
    await archive_session()

    # Post "Session Offline" to archive channel
    try:
        archive_ch = await bot.fetch_channel(archive_channel_id)
        if archive_ch:
            offline_embed = discord.Embed(
                title="Session Offline 🔴",
                description="Thank you so much for a great session. We will see you next time!",
                color=0xe74c3c
            )
            await archive_ch.send(embed=offline_embed)
    except Exception as e:
        print(f"❌ Could not post offline message: {e}")

    # Update the session embed to show "Session has ended" and disable buttons
    if event_message:
        try:
            embed = build_embed()
            embed.set_footer(text="🔴 Session has ended")
            # Send with no view (disables all buttons)
            await event_message.edit(embed=embed, view=None)
        except Exception as e:
            print(f"❌ Could not update session embed: {e}")

    print("🔴 Session ended")

    # Post to status channel if configured
    if status_channel_id:
        try:
            status_ch = await bot.fetch_channel(status_channel_id)
            sname = SESSION_TYPES.get(session_type, SESSION_TYPES['hunt'])['label']
            msg = status_stop_msg.replace('{name}', sname)
            stop_embed = discord.Embed(
                title=f"{sname} Ended 🔴",
                description=msg,
                color=0xe74c3c
            )
            stop_embed.add_field(name="Attended", value=str(len(attending_ids)), inline=True)
            stop_embed.add_field(name="Standby", value=str(len(standby_ids)), inline=True)
            await status_ch.send(embed=stop_embed)
        except Exception as e:
            print(f"❌ Could not post session end to status channel: {e}")

# Initialize state
load_state()
load_history()

# References to be populated at runtime
attending = []
standby = []
not_attending = []
pending_offer = None
event_message = None
schedule_view = None
countdown_task = None  # asyncio.Task for live countdown
_leaderboard_cooldown = None  # Cooldown for leaderboard button (60s)

# ----------------------------
# Helper: next run datetime
# ----------------------------
def next_run_time(target_hour: int, target_weekday: int):
    now = datetime.now(EST)
    today_weekday = now.weekday()
    days_ahead = target_weekday - today_weekday
    if days_ahead < 0 or (days_ahead == 0 and now.hour >= target_hour):
        days_ahead += 7
    target_date = now + timedelta(days=days_ahead)
    return target_date.replace(hour=target_hour, minute=0, second=0, microsecond=0)

# ----------------------------
# Guild restrictions
# ----------------------------
@bot.event
async def on_guild_join(guild):
    if guild.id not in ALLOWED_GUILDS:
        await guild.leave()

@bot.check
async def globally_allowed(ctx):
    return ctx.guild and ctx.guild.id in ALLOWED_GUILDS

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f" Missing argument: {error.param.name}", delete_after=10)
        return
    print(f"Command error in {ctx.command}: {error}")
    try:
        await ctx.send(f"Error: {error}", delete_after=10)
    except:
        pass

# ----------------------------
# Sync IDs to User objects
# ----------------------------
async def sync_users_from_ids():
    global attending, standby, not_attending, pending_offer, event_message

    attending = []
    standby = []
    not_attending = []
    pending_offer = None
    event_message = None

    guild = None
    for g in bot.guilds:
        if g.id in ALLOWED_GUILDS:
            guild = g
            break

    if not guild:
        print("❌ No allowed guild found")
        return

    for uid in attending_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                attending.append(member)
        except:
            pass

    for uid in standby_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                standby.append(member)
        except:
            pass

    for uid in not_attending_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                not_attending.append(member)
        except:
            pass

    if pending_offer_id:
        try:
            member = await guild.fetch_member(pending_offer_id)
            if member:
                pending_offer = member
        except:
            pass

    if event_message_id and event_channel_id:
        try:
            channel = await bot.fetch_channel(event_channel_id)
            if channel:
                event_message = await channel.fetch_message(event_message_id)
                print(f"✅ Restored event message: {event_message_id}")
        except Exception as e:
            print(f"❌ Could not restore event message: {e}")

    print(f"✅ Synced users: {len(attending)} attending, {len(standby)} standby, {len(not_attending)} not attending")

    # Keep ID lists in sync after member resolution
    # (if any member couldn't be fetched, attending_ids must reflect the reduced list)
    sync_ids_from_users()

def sync_ids_from_users():
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id

    # Fallback assignment for Nesting sessions: if an attending user has no explicit role, default to Protector.
    if session_type == 'nesting':
        for u in attending:
            if u.id not in nest_parent_ids and u.id not in nest_baby_ids and u.id not in nest_protector_ids:
                nest_protector_ids.append(u.id)

    attending_ids = [u.id for u in attending]
    standby_ids = [u.id for u in standby]
    not_attending_ids = [u.id for u in not_attending]
    pending_offer_id = pending_offer.id if pending_offer else None

    save_state()

# ----------------------------
# Build embed (with countdown + streaks)
# ----------------------------
def build_embed():
    stype = SESSION_TYPES.get(session_type, SESSION_TYPES['hunt'])
    title = f"{stype['emoji']} {session_name or 'Session Sign-Up'}"

    # Add session type label
    if session_type != 'hunt':
        title += f" — {stype['label']}"

    # Add countdown if session time is set
    if session_dt_str:
        try:
            dt = datetime.fromisoformat(session_dt_str)
            unix_ts = int(dt.timestamp())
            title += f"\n⏰ Starts <t:{unix_ts}:R>"
        except:
            pass

    # Color changes based on session state
    if session_ended:
        color = 0xe74c3c  # red
    elif session_has_started():
        color = 0xf39c12  # orange/amber - in progress
    else:
        color = stype['color']  # use session type color

    embed = discord.Embed(title=title, color=color)

    # ── NESTING MODE ── shows Parent/Babies/Protectors
    if session_type == 'nesting':
        # Parents
        parents = [u for u in attending if u.id in nest_parent_ids]
        babies = [u for u in attending if u.id in nest_baby_ids]
        protectors = [u for u in attending if u.id in nest_protector_ids]

        if parents:
            parent_text = "\n".join(f"`{i+1}.` {u.mention} 🦕" for i, u in enumerate(parents))
        else:
            parent_text = "*No parent designated — use `!parent @user`*"

        if babies:
            baby_lines = []
            for i, u in enumerate(babies):
                checkin_mark = " ✅" if u.id in checked_in_ids else ""
                baby_lines.append(f"`{i+1}.` {u.mention} 🐣{checkin_mark}{streak_badge(u.id)}")
            baby_text = "\n".join(baby_lines)
        else:
            baby_text = "*No babies yet — click Join as Baby!*"

        if protectors:
            prot_text = "\n".join(f"`{i+1}.` {u.mention} 🛡️{streak_badge(u.id)}" for i, u in enumerate(protectors))
        else:
            prot_text = "*None*"

        embed.add_field(
            name=f"\u2800\n🦕 Parent(s) ({len(parents)})",
            value=parent_text + "\n\u2800",
            inline=False
        )
        embed.add_field(
            name=f"🐣 Babies ({len(babies)})",
            value=baby_text + "\n\u2800",
            inline=False
        )
        embed.add_field(
            name=f"🛡️ Protectors ({len(protectors)})",
            value=prot_text + "\n\u2800",
            inline=False
        )
    else:
        # ── REGULAR MODE ── (Hunt, Growth, PvP, Migration)
        if attending:
            attend_lines = []
            for i, user in enumerate(attending):
                checkin_mark = " ✅" if user.id in checked_in_ids else ""
                attend_lines.append(f"`{i+1}.` {user.mention}{checkin_mark}{streak_badge(user.id)}")
            attend_text = "\n".join(attend_lines)
        else:
            attend_text = "*No one yet — be the first!*"

        if standby:
            standby_text = "\n".join(
                f"`{i+1}.` {user.mention}{streak_badge(user.id)}"
                for i, user in enumerate(standby)
            )
        else:
            standby_text = "*Empty*"

        embed.add_field(
            name=f"\u2800\n✅ Attending ({len(attending)}/{MAX_ATTENDING})",
            value=attend_text + "\n\u2800",
            inline=False
        )
        embed.add_field(
            name=f"⏳ Standby ({len(standby)})",
            value=standby_text + "\n\u2800",
            inline=False
        )

    # Not attending (both modes)
    if not_attending:
        not_attend_text = "\n".join(f"{user.mention}" for user in not_attending)
    else:
        not_attend_text = "*None*"

    embed.add_field(
        name=f"😞 Not Attending ({len(not_attending)})",
        value=not_attend_text,
        inline=False
    )

    # Status footer
    if session_ended:
        embed.set_footer(text="🔴 Session has ended")
    elif session_has_started():
        embed.set_footer(text="🟢 Session is live! Check-in enabled.")
    else:
        # No-show warning footer
        auto_standby_users = [u for u in attending if is_auto_standby(u.id)]
        if auto_standby_users:
            embed.set_footer(text="⚠️ Some users have high no-show counts")
        else:
            footer = "Click a button below to sign up!"
            if session_type == 'nesting':
                footer = "🥚 Nesting mode — parents protect the nest, babies grow!"
            embed.set_footer(text=footer)

    return embed

# ----------------------------
# Standby promotion
# ----------------------------
async def offer_next_standby():
    global pending_offer
    while standby and len(attending) < MAX_ATTENDING and pending_offer is None:
        next_user = standby.pop(0)
        pending_offer = next_user
        sync_ids_from_users()
        try:
            await next_user.send(
                "🎉 A spot opened up! Do you want to accept it?",
                view=OfferView(next_user)
            )
            break
        except:
            not_attending.append(next_user)
            pending_offer = None
            sync_ids_from_users()
            continue

# ----------------------------
# DM Offer View
# ----------------------------
class OfferView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=None)
        self.user = user

    @discord.ui.button(label="Accept Spot", style=discord.ButtonStyle.success, emoji="✅", custom_id="offer_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        global pending_offer
        if interaction.user != self.user:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return
        attending.append(self.user)
        pending_offer = None
        sync_ids_from_users()
        await interaction.response.edit_message(content="✅ You are now ATTENDING!", view=None)
        if schedule_view:
            await schedule_view.update_embed()

    @discord.ui.button(label="Decline Spot", style=discord.ButtonStyle.danger, emoji="❌", custom_id="offer_decline")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        global pending_offer
        if interaction.user != self.user:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return
        not_attending.append(self.user)
        pending_offer = None
        sync_ids_from_users()
        await interaction.response.edit_message(content="❌ You declined the spot.", view=None)
        if schedule_view:
            await schedule_view.update_embed()
        await offer_next_standby()

# ----------------------------
# Check-In View
# ----------------------------
class CheckInView(discord.ui.View):
    """DM-based check-in button sent to each attending user individually."""
    def __init__(self, user_id=None):
        super().__init__(timeout=None)
        self.target_user_id = user_id

    @discord.ui.button(label="Check In", style=discord.ButtonStyle.success, emoji="🟢", custom_id="checkin_button")
    async def check_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user.id not in attending_ids:
            await interaction.response.send_message("You're not on the attending list.", ephemeral=True)
            return
        if user.id in checked_in_ids:
            await interaction.response.send_message("You're already checked in! ✅", ephemeral=True)
            return
        checked_in_ids.append(user.id)
        save_state()
        # Disable the button after check-in
        button.disabled = True
        button.label = "Checked In ✅"
        button.style = discord.ButtonStyle.secondary
        sname = SESSION_TYPES.get(session_type, SESSION_TYPES['hunt'])['label']
        await interaction.response.edit_message(
            content=f"✅ **You're checked in for the {sname}!** See you in the session.",
            view=self
        )
        # Refresh the session embed to show the checkmark
        if schedule_view:
            await schedule_view.update_embed()

# ----------------------------
# Swap View (DM)
# ----------------------------
class SwapView(discord.ui.View):
    def __init__(self, requester_id, target_id):
        super().__init__(timeout=300)  # 5 min timeout
        self.requester_id = requester_id
        self.target_id = target_id

    @discord.ui.button(label="Accept Swap", style=discord.ButtonStyle.success, emoji="🔄", custom_id="swap_accept")
    async def accept_swap(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return
        # Find both users and swap their positions
        req_in_attending = any(u.id == self.requester_id for u in attending)
        req_in_standby = any(u.id == self.requester_id for u in standby)
        tgt_in_attending = any(u.id == self.target_id for u in attending)
        tgt_in_standby = any(u.id == self.target_id for u in standby)

        # Do the swap
        if req_in_attending and tgt_in_standby:
            req_user = next(u for u in attending if u.id == self.requester_id)
            tgt_user = next(u for u in standby if u.id == self.target_id)
            attending.remove(req_user)
            standby.remove(tgt_user)
            attending.append(tgt_user)
            standby.append(req_user)
        elif req_in_standby and tgt_in_attending:
            req_user = next(u for u in standby if u.id == self.requester_id)
            tgt_user = next(u for u in attending if u.id == self.target_id)
            standby.remove(req_user)
            attending.remove(tgt_user)
            standby.append(tgt_user)
            attending.append(req_user)
        else:
            await interaction.response.edit_message(content="❌ Swap failed — positions changed.", view=None)
            return

        sync_ids_from_users()
        await interaction.response.edit_message(content="✅ Swap complete!", view=None)
        if schedule_view:
            await schedule_view.update_embed()

        # Notify requester
        try:
            req_user_obj = await bot.fetch_user(self.requester_id)
            await req_user_obj.send("✅ Your swap was accepted!")
        except:
            pass

    @discord.ui.button(label="Decline Swap", style=discord.ButtonStyle.danger, emoji="❌", custom_id="swap_decline")
    async def decline_swap(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target_id:
            await interaction.response.send_message("This is not for you.", ephemeral=True)
            return
        await interaction.response.edit_message(content="❌ Swap declined.", view=None)
        try:
            req_user_obj = await bot.fetch_user(self.requester_id)
            await req_user_obj.send("❌ Your swap request was declined.")
        except:
            pass

# ----------------------------
# Reminder Confirm/Drop View (DM)
# ----------------------------
class ReminderView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Still Coming!", style=discord.ButtonStyle.success, emoji="👍", custom_id="reminder_confirm")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        await interaction.response.edit_message(content="👍 Great, see you there!", view=None)

    @discord.ui.button(label="Can't Make It", style=discord.ButtonStyle.danger, emoji="👋", custom_id="reminder_drop")
    async def drop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return
        # Remove from attending
        user = interaction.user
        to_remove = [u for u in attending if u.id == user.id]
        for u in to_remove:
            attending.remove(u)
        if user not in not_attending:
            # Need a Member object; user from DM is a User not Member
            not_attending_ids.append(user.id)
        sync_ids_from_users()
        await interaction.response.edit_message(content="👋 Removed from attending. Your spot has been offered to standby.", view=None)
        await offer_next_standby()
        if schedule_view:
            await schedule_view.update_embed()

# ----------------------------
# Leaderboard Image Renderer
# ----------------------------
def _render_leaderboard_image(entries, clicker_id):
    """Render a leaderboard table as a PNG image. Returns a BytesIO object."""
    # --- Configuration ---
    BG_COLOR = (30, 33, 36)         # Discord dark bg
    HEADER_COLOR = (88, 101, 242)   # Blurple header
    ROW_EVEN = (44, 47, 51)         # Dark row
    ROW_ODD = (54, 57, 63)          # Slightly lighter row
    HIGHLIGHT_ROW = (32, 58, 107)   # Blue highlight for clicker
    TEXT_COLOR = (255, 255, 255)     # White text
    DIM_TEXT = (185, 187, 190)      # Dimmer text
    GOLD = (255, 215, 0)
    SILVER = (192, 192, 192)
    BRONZE = (205, 127, 50)
    MEDAL_COLORS = [GOLD, SILVER, BRONZE]
    GREEN = (67, 181, 129)
    RED = (240, 71, 71)
    ORANGE = (255, 165, 0)

    # Try to load a nice font; fall back to default
    font_size = 16
    header_font_size = 14
    title_font_size = 22
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", header_font_size)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_font_size)
    except OSError:
        font = ImageFont.load_default()
        font_bold = font
        header_font = font
        title_font = font

    # --- Column layout ---
    col_widths = [55, 200, 80, 80, 65, 65]  # Rank, Name, Attended, No-Show, Rate, Streak
    col_headers = ["Rank", "Name", "Attend", "No-Show", "Rate", "Streak"]
    row_height = 32
    padding = 12
    title_height = 45
    header_height = 30
    table_width = sum(col_widths) + padding * 2
    table_height = title_height + header_height + row_height * len(entries) + padding

    img = Image.new("RGB", (table_width, table_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Title bar with accent ---
    draw.rectangle([(0, 0), (4, title_height)], fill=HEADER_COLOR)  # left accent bar
    draw.text((padding + 6, 10), "Attendance Leaderboard", fill=TEXT_COLOR, font=title_font)

    # --- Column headers ---
    y = title_height
    draw.rectangle([(0, y), (table_width, y + header_height)], fill=HEADER_COLOR)
    x = padding
    for i, header in enumerate(col_headers):
        draw.text((x + 4, y + 7), header, fill=TEXT_COLOR, font=header_font)
        x += col_widths[i]

    # --- Data rows ---
    y += header_height
    medal_labels = ["#1", "#2", "#3"]
    for idx, (uid_str, name, attended, no_shows, rate, streak) in enumerate(entries):
        is_clicker = uid_str == clicker_id
        if is_clicker:
            row_color = HIGHLIGHT_ROW
        elif idx % 2 == 0:
            row_color = ROW_EVEN
        else:
            row_color = ROW_ODD

        draw.rectangle([(0, y), (table_width, y + row_height)], fill=row_color)
        # Left accent for clicker
        if is_clicker:
            draw.rectangle([(0, y), (4, y + row_height)], fill=GOLD)

        x = padding
        # Rank column
        if idx < 3:
            rank_text = medal_labels[idx]
            rank_color = MEDAL_COLORS[idx]
        else:
            rank_text = f"#{idx + 1}"
            rank_color = DIM_TEXT
        draw.text((x + 4, y + 7), rank_text, fill=rank_color, font=font_bold)
        x += col_widths[0]

        # Name column
        display_name = name[:18] + ".." if len(name) > 18 else name
        if is_clicker:
            display_name = ">>> " + display_name
        name_font = font_bold if is_clicker else font
        name_color = GOLD if is_clicker else TEXT_COLOR
        draw.text((x + 4, y + 7), display_name, fill=name_color, font=name_font)
        x += col_widths[1]

        # Attended column
        draw.text((x + 4, y + 7), str(attended), fill=GREEN, font=font)
        x += col_widths[2]

        # No-Show column
        ns_color = RED if no_shows > 0 else DIM_TEXT
        draw.text((x + 4, y + 7), str(no_shows), fill=ns_color, font=font)
        x += col_widths[3]

        # Rate column
        rate_color = GREEN if rate >= 80 else (ORANGE if rate >= 50 else RED)
        draw.text((x + 4, y + 7), f"{rate:.0f}%", fill=rate_color, font=font)
        x += col_widths[4]

        # Streak column
        if streak >= 3:
            streak_text = f"x{streak}"
            draw.text((x + 4, y + 7), streak_text, fill=ORANGE, font=font_bold)
        else:
            draw.text((x + 4, y + 7), str(streak), fill=DIM_TEXT, font=font)

        y += row_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ----------------------------
# Nesting Leaderboard Image Renderer
# ----------------------------
def _render_nesting_leaderboard_image(entries, clicker_id):
    """Render a Nesting leaderboard table as a PNG image. Returns a BytesIO object."""
    # --- Configuration ---
    BG_COLOR = (30, 33, 36)
    HEADER_COLOR = (241, 196, 15)   # Yellow/Gold for nesting
    ROW_EVEN = (44, 47, 51)
    ROW_ODD = (54, 57, 63)
    HIGHLIGHT_ROW = (133, 108, 12)  # Darker yellow for clicker
    TEXT_COLOR = (255, 255, 255)
    DIM_TEXT = (185, 187, 190)
    GOLD = (255, 215, 0)
    SILVER = (192, 192, 192)
    BRONZE = (205, 127, 50)
    MEDAL_COLORS = [GOLD, SILVER, BRONZE]

    font_size = 16
    header_font_size = 14
    title_font_size = 22
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", header_font_size)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", title_font_size)
        emoji_font = ImageFont.truetype("/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", 20) # Attempt to load emoji font
    except OSError:
        font = ImageFont.load_default()
        font_bold = font
        header_font = font
        title_font = font
        emoji_font = font

    # --- Column layout ---
    col_widths = [55, 200, 95, 95, 95]  # Rank, Name, Parents, Babies, Protectors
    col_headers = ["Rank", "Name", "Parents", "Babies", "Protectors"]
    row_height = 32
    padding = 12
    title_height = 45
    header_height = 30
    table_width = sum(col_widths) + padding * 2
    table_height = title_height + header_height + row_height * len(entries) + padding

    img = Image.new("RGB", (table_width, table_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # --- Title bar with accent ---
    draw.rectangle([(0, 0), (4, title_height)], fill=HEADER_COLOR)
    draw.text((padding + 6, 10), "🥚 Nesting Leaderboard", fill=TEXT_COLOR, font=title_font)

    # --- Column headers ---
    y = title_height
    draw.rectangle([(0, y), (table_width, y + header_height)], fill=HEADER_COLOR)
    x = padding
    for i, header in enumerate(col_headers):
        # We can't render emojis easily with Pil without specific setups, so we use text labels
        # but to keep it clean, we just write the text header in Black for contrast against yellow
        draw.text((x + 4, y + 7), header, fill=(0,0,0), font=header_font)
        x += col_widths[i]

    # --- Data rows ---
    y += header_height
    medal_labels = ["#1", "#2", "#3"]
    for idx, (uid_str, name, parents, babies, protectors) in enumerate(entries):
        is_clicker = uid_str == clicker_id
        if is_clicker:
            row_color = HIGHLIGHT_ROW
        elif idx % 2 == 0:
            row_color = ROW_EVEN
        else:
            row_color = ROW_ODD

        draw.rectangle([(0, y), (table_width, y + row_height)], fill=row_color)
        if is_clicker:
            draw.rectangle([(0, y), (4, y + row_height)], fill=GOLD)

        x = padding
        # Rank column
        if idx < 3:
            rank_text = medal_labels[idx]
            rank_color = MEDAL_COLORS[idx]
        else:
            rank_text = f"#{idx + 1}"
            rank_color = DIM_TEXT
        draw.text((x + 4, y + 7), rank_text, fill=rank_color, font=font_bold)
        x += col_widths[0]

        # Name column
        display_name = name[:18] + ".." if len(name) > 18 else name
        if is_clicker:
            display_name = ">>> " + display_name
        name_font = font_bold if is_clicker else font
        name_color = GOLD if is_clicker else TEXT_COLOR
        draw.text((x + 4, y + 7), display_name, fill=name_color, font=name_font)
        x += col_widths[1]

        # Parents
        draw.text((x + 4, y + 7), str(parents), fill=TEXT_COLOR, font=font)
        x += col_widths[2]

        # Babies
        draw.text((x + 4, y + 7), str(babies), fill=TEXT_COLOR, font=font)
        x += col_widths[3]

        # Protectors
        draw.text((x + 4, y + 7), str(protectors), fill=TEXT_COLOR, font=font)

        y += row_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ----------------------------
# Dino Battle Image Renderer
# ----------------------------
def _render_vs_image(dino_a, dino_b):
    """Render a side-by-side trading card battle image with fantasy frame. Returns a BytesIO object."""
    CARD_WIDTH = 300
    CARD_HEIGHT = 450
    PADDING = 40
    CENTER_GAP = 80
    TOTAL_WIDTH = (CARD_WIDTH * 2) + (PADDING * 2) + CENTER_GAP
    TOTAL_HEIGHT = CARD_HEIGHT + (PADDING * 2)

    BG_COLOR = (20, 22, 25)
    TEXT_COLOR = (255, 255, 255)

    img = Image.new("RGBA", (TOTAL_WIDTH, TOTAL_HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)

    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_stat_label = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
        font_stat_val = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
        font_vs = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 42)
    except OSError:
        font_name = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        font_stat_label = ImageFont.load_default()
        font_stat_val = ImageFont.load_default()
        font_vs = ImageFont.load_default()

    # Build card frame programmatically (true alpha transparency)
    def _build_card_frame(w, h):
        """Draw an ornate card frame with true transparency using Pillow."""
        frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        fd = ImageDraw.Draw(frame)
        GOLD = (190, 155, 60, 255)
        GOLD_DIM = (140, 110, 40, 200)
        DARK = (18, 18, 22, 230)
        BORDER_W = 6

        # Outer border — gold rounded rectangle
        fd.rounded_rectangle([(0, 0), (w-1, h-1)], radius=14, outline=GOLD, width=BORDER_W)
        # Inner border — darker inset
        fd.rounded_rectangle([(BORDER_W+2, BORDER_W+2), (w-BORDER_W-3, h-BORDER_W-3)],
                             radius=10, outline=GOLD_DIM, width=2)

        # Name banner bar (dark semi-transparent strip across middle)
        banner_y = int(h * 0.47)
        banner_h = 48
        fd.rectangle([(BORDER_W+3, banner_y), (w-BORDER_W-3, banner_y + banner_h)], fill=DARK)
        fd.line([(BORDER_W+3, banner_y), (w-BORDER_W-3, banner_y)], fill=GOLD, width=2)
        fd.line([(BORDER_W+3, banner_y+banner_h), (w-BORDER_W-3, banner_y+banner_h)], fill=GOLD, width=2)

        # Stats area background (lower portion, semi-transparent)
        stats_top = banner_y + banner_h + 8
        fd.rounded_rectangle(
            [(BORDER_W+6, stats_top), (w-BORDER_W-6, h-BORDER_W-6)],
            radius=8, fill=(10, 10, 15, 180)
        )

        # Corner diamonds (decorative)
        for cx, cy in [(16, 16), (w-16, 16), (16, h-16), (w-16, h-16)]:
            fd.polygon([(cx, cy-6), (cx+6, cy), (cx, cy+6), (cx-6, cy)], fill=GOLD)

        # Top center gem
        gem_x, gem_y = w // 2, 10
        fd.polygon([(gem_x, gem_y-5), (gem_x+8, gem_y+3), (gem_x, gem_y+11), (gem_x-8, gem_y+3)], fill=GOLD)

        return frame

    card_frame = _build_card_frame(CARD_WIDTH, CARD_HEIGHT)

    def draw_card(x_offset, dino, side="left"):
        # Diet tint behind everything
        tint_color = (88, 28, 28, 180) if dino['type'] == 'carnivore' else (28, 68, 48, 180)
        tint = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), tint_color)
        img.paste(tint, (x_offset, PADDING), tint)

        # Avatar — fill the upper portrait area of the frame
        avatar_region = (CARD_WIDTH - 40, int(CARD_HEIGHT * 0.50))
        avatar_y = PADDING + 20
        avatar_x = x_offset + 20
        try:
            # Check custom frame first
            custom_frame_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", "frames", f"{side}_frame.png")
            has_custom_frame = os.path.exists(custom_frame_path)

            avatar_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", f"{dino['id']}.png")
            if not os.path.exists(avatar_path):
                avatar_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", "defaults", f"{dino['id']}.png")
            avatar = Image.open(avatar_path).convert("RGBA")
            avatar = avatar.resize(avatar_region, Image.Resampling.LANCZOS)

            # Apply rounded rectangle mask to avatar
            mask = Image.new("L", avatar_region, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.rounded_rectangle([(0, 0), avatar_region], radius=12, fill=255)
            img.paste(avatar, (avatar_x, avatar_y), mask)

            # If custom frame exists, overlay it on top of avatar
            if has_custom_frame:
                try:
                    custom_frame = Image.open(custom_frame_path).convert("RGBA")
                    custom_frame = custom_frame.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)
                    img.paste(custom_frame, (x_offset, PADDING), custom_frame)
                except Exception:
                    pass
        except Exception:
            draw.rounded_rectangle(
                [(avatar_x, avatar_y), (avatar_x + avatar_region[0], avatar_y + avatar_region[1])],
                radius=12, fill=(40, 40, 40)
            )
            draw.text((avatar_x + avatar_region[0]//2 - 30, avatar_y + avatar_region[1]//2 - 8),
                       "No Image", fill=TEXT_COLOR, font=font_sub)

        # Overlay the programmatic card frame on top (true alpha)
        img.paste(card_frame, (x_offset, PADDING), card_frame)

        # Name — positioned in the name banner area of the frame
        import re as _re
        full_name = dino['name']
        m = _re.match(r'^(.+?)\s*\((.+)\)$', full_name)
        if m:
            base_name = m.group(1).strip()
            subtitle = f"({m.group(2).strip()})"
        else:
            base_name = full_name
            subtitle = None

        # Shrink font if name is too wide
        fn = font_name
        name_bbox = draw.textbbox((0, 0), base_name, font=fn)
        name_w = name_bbox[2] - name_bbox[0]
        if name_w > CARD_WIDTH - 40:
            try:
                fn = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
                name_bbox = draw.textbbox((0, 0), base_name, font=fn)
                name_w = name_bbox[2] - name_bbox[0]
            except OSError:
                pass

        # Name banner area (below portrait, in frame banner region)
        name_y = PADDING + int(CARD_HEIGHT * 0.50)
        name_x = x_offset + (CARD_WIDTH - name_w) // 2

        # Shadow + main text for legibility
        for dx, dy in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
            draw.text((name_x+dx, name_y+dy), base_name, fill=(0,0,0), font=fn)
        draw.text((name_x, name_y), base_name, fill=TEXT_COLOR, font=fn)

        if subtitle:
            sub_bbox = draw.textbbox((0, 0), subtitle, font=font_sub)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_x = x_offset + (CARD_WIDTH - sub_w) // 2
            sub_y = name_y + 24
            for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
                draw.text((sub_x+dx, sub_y+dy), subtitle, fill=(0,0,0), font=font_sub)
            draw.text((sub_x, sub_y), subtitle, fill=(180, 180, 180), font=font_sub)

        # Stats — rendered as compact badges in the lower portion of the card
        stats_y = PADDING + int(CARD_HEIGHT * 0.62)
        stat_spacing = 27
        stats = [
            ("CW", str(dino.get('cw', 3000)), (155, 89, 182)),
            ("HP", str(dino.get('hp', 500)), (46, 204, 113)),
            ("ATK", str(dino.get('atk', 50)), (231, 76, 60)),
            ("DEF", str(dino.get('armor', 1.0)), (52, 152, 219)),
            ("SPD", str(dino.get('spd', 500)), (241, 196, 15)),
        ]

        for label, val, color in stats:
            # Stat badge background pill
            pill_x = x_offset + 30
            pill_w = CARD_WIDTH - 60
            draw.rounded_rectangle(
                [(pill_x, stats_y), (pill_x + pill_w, stats_y + 24)],
                radius=4, fill=(15, 15, 20, 200)
            )

            # Label on left
            for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
                draw.text((pill_x + 8 + dx, stats_y + 4 + dy), label, fill=(0,0,0), font=font_stat_label)
            draw.text((pill_x + 8, stats_y + 4), label, fill=color, font=font_stat_label)

            # Value on right
            val_bbox = draw.textbbox((0, 0), val, font=font_stat_val)
            val_w = val_bbox[2] - val_bbox[0]
            val_x = pill_x + pill_w - val_w - 8
            for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
                draw.text((val_x + dx, stats_y + 2 + dy), val, fill=(0,0,0), font=font_stat_val)
            draw.text((val_x, stats_y + 2), val, fill=TEXT_COLOR, font=font_stat_val)

            # Small colored accent bar
            bar_w = min(pill_w - 70, int(pill_w * 0.4))
            draw.rectangle([(pill_x + 50, stats_y + 19), (pill_x + 50 + bar_w, stats_y + 22)], fill=color)

            stats_y += stat_spacing

    # Draw Card A
    draw_card(PADDING, dino_a, "left")

    # Draw VS badge in center
    vs_x = PADDING + CARD_WIDTH + (CENTER_GAP // 2)
    vs_y = (TOTAL_HEIGHT // 2)
    # VS circle background
    circle_r = 28
    draw.ellipse(
        [(vs_x - circle_r, vs_y - circle_r), (vs_x + circle_r, vs_y + circle_r)],
        fill=(231, 76, 60), outline=(241, 196, 15), width=3
    )
    vs_bbox = draw.textbbox((0, 0), "VS", font=font_vs)
    vs_tw = vs_bbox[2] - vs_bbox[0]
    vs_th = vs_bbox[3] - vs_bbox[1]
    draw.text((vs_x - vs_tw // 2, vs_y - vs_th // 2 - 4), "VS", fill=TEXT_COLOR, font=font_vs)

    # Draw Card B
    draw_card(PADDING + CARD_WIDTH + CENTER_GAP, dino_b, "right")

    # Convert to RGB for PNG save (Discord doesn't handle RGBA well)
    final = Image.new("RGB", img.size, BG_COLOR)
    final.paste(img, (0, 0), img)

    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ----------------------------
# Main Schedule View
# ----------------------------
class ScheduleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.build_buttons()

    def build_buttons(self):
        self.clear_items()
        if session_type == 'nesting':
            # 1. Parent
            btn_parent = discord.ui.Button(label="Join as Parent", style=discord.ButtonStyle.success, emoji="🦕", custom_id="nest_parent")
            btn_parent.callback = self.join_parent
            self.add_item(btn_parent)

            # 2. Baby
            btn_baby = discord.ui.Button(label="Join as Baby", style=discord.ButtonStyle.primary, emoji="🐣", custom_id="nest_baby")
            btn_baby.callback = self.join_baby
            self.add_item(btn_baby)

            # 3. Protector
            btn_prot = discord.ui.Button(label="Join as Protector", style=discord.ButtonStyle.secondary, emoji="🛡️", custom_id="nest_protector")
            btn_prot.callback = self.join_protector
            self.add_item(btn_prot)

            # 4. Relieve Spot (Row 1)
            btn_relieve = discord.ui.Button(label="Relieve Slot", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="schedule_relieve", row=1)
            btn_relieve.callback = self.relieve_spot
            self.add_item(btn_relieve)
            
        else:
            # Standard Session Buttons
            btn_attend = discord.ui.Button(style=discord.ButtonStyle.success, custom_id="schedule_attend")
            btn_attend.callback = self.attend
            
            btn_standby = discord.ui.Button(label="Standby", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="schedule_standby")
            btn_standby.callback = self.join_standby
            
            btn_not = discord.ui.Button(style=discord.ButtonStyle.danger, custom_id="schedule_not_attend")
            btn_not.callback = self.not_attend
            
            btn_relieve = discord.ui.Button(label="Relieve Spot", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="schedule_relieve")
            btn_relieve.callback = self.relieve_spot

            if session_type == 'growth':
                btn_attend.label, btn_attend.emoji = "Join Growth", "🌱"
                btn_not.label, btn_not.emoji = "Skipping", "😞"
            elif session_type == 'pvp':
                btn_attend.label, btn_attend.emoji = "Join Battle", "⚔️"
                btn_not.label, btn_not.emoji = "Retreating", "😞"
            elif session_type == 'migration':
                btn_attend.label, btn_attend.emoji = "Join Herd", "🏃"
                btn_not.label, btn_not.emoji = "Staying Behind", "😞"
            else:
                btn_attend.label, btn_attend.emoji = "Join Hunt", "🦴"
                btn_not.label, btn_not.emoji = "Can't Make It", "😞"

            self.add_item(btn_attend)
            self.add_item(btn_standby)
            self.add_item(btn_not)
            self.add_item(btn_relieve)

        # Admin Controls (Row 1) (Same for all)
        row = 1 if session_type != 'nesting' else 1
        btn_end = discord.ui.Button(label="End Session", style=discord.ButtonStyle.secondary, emoji="🔴", custom_id="schedule_end_session", row=row)
        btn_end.callback = self.end_session_btn
        self.add_item(btn_end)

        btn_menu = discord.ui.Button(label="Menu", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="schedule_menu", row=row)
        btn_menu.callback = self.menu_btn
        self.add_item(btn_menu)

        btn_lb = discord.ui.Button(label="Leaderboard", style=discord.ButtonStyle.primary, emoji="📊", custom_id="schedule_leaderboard", row=row)
        btn_lb.callback = self.leaderboard_btn
        self.add_item(btn_lb)

    async def update_embed(self):
        if event_message:
            self.build_buttons()
            await event_message.edit(embed=build_embed(), view=self)

    # --- Nesting Specific Callbacks ---
    async def join_parent(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        if user.id in nest_parent_ids:
            await interaction.response.send_message("You are already a Parent!", ephemeral=True)
            return
        if len(nest_parent_ids) >= 2:
            await interaction.response.send_message("❌ The nest already has 2 parents! You cannot join as a parent.", ephemeral=True)
            return
        
        await self._remove_from_other_nest_roles(user.id)
        nest_parent_ids.append(user.id)
        await self._ensure_attending(user, interaction, "🦕 Joined as a Nest Parent!")

    async def join_baby(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        if user.id in nest_baby_ids:
            await interaction.response.send_message("You are already a Baby!", ephemeral=True)
            return
            
        await self._remove_from_other_nest_roles(user.id)
        nest_baby_ids.append(user.id)
        await self._ensure_attending(user, interaction, "🐣 Joined as a Baby!")

    async def join_protector(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        if user.id in nest_protector_ids:
            await interaction.response.send_message("You are already a Protector!", ephemeral=True)
            return
            
        await self._remove_from_other_nest_roles(user.id)
        nest_protector_ids.append(user.id)
        await self._ensure_attending(user, interaction, "🛡️ Joined as a Protector!")

    async def _remove_from_other_nest_roles(self, uid):
        if uid in nest_parent_ids: nest_parent_ids.remove(uid)
        if uid in nest_baby_ids: nest_baby_ids.remove(uid)
        if uid in nest_protector_ids: nest_protector_ids.remove(uid)

    async def _ensure_attending(self, user, interaction, success_msg):
        # Move user to attending if not already there, handle standby/not_attending logic
        if user in not_attending:
            not_attending.remove(user)
        if user in standby:
            standby.remove(user)
            
        if user not in attending:
            if len(attending) < MAX_ATTENDING and pending_offer is None:
                attending.append(user)
            else:
                # If full, put them back on standby and don't assign role
                standby.append(user)
                await self._remove_from_other_nest_roles(user.id)
                sync_ids_from_users()
                await interaction.response.send_message("Attending is full! You've been placed on standby.", ephemeral=True)
                await self.update_embed()
                return

        sync_ids_from_users()
        await interaction.response.send_message(success_msg, ephemeral=True)
        await self.update_embed()

    async def _handle_common_checks(self, interaction: discord.Interaction):
        if session_has_started() or session_ended:
            msg = "🔴 Session has ended." if session_ended else "🔒 Session has already started — sign-ups are closed."
            await interaction.response.send_message(msg, ephemeral=True)
            return True
        return False

    # --- Standard Callbacks ---
    async def attend(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        
        if user in attending:
            await interaction.response.send_message("You're already attending!", ephemeral=True)
            return
        if user in standby:
            if len(attending) < MAX_ATTENDING and pending_offer is None:
                standby.remove(user)
                attending.append(user)
                sync_ids_from_users()
                await interaction.response.send_message("✅ Moved from standby to attending!", ephemeral=True)
                await self.update_embed()
                return
            else:
                await interaction.response.send_message("You're on standby — attending is full right now.", ephemeral=True)
                return
        if user in not_attending:
            not_attending.remove(user)

        if is_auto_standby(user.id):
            stats = get_user_stats(user.id)
            rate = int((stats['no_shows'] / stats['total_signups']) * 100)
            standby.append(user)
            sync_ids_from_users()
            await interaction.response.send_message(
                f"⚠️ Your no-show rate is **{rate}%** — you've been placed on **standby**. "
                f"Check in to future sessions to lower your rate!",
                ephemeral=True
            )
            await self.update_embed()
            return

        if len(attending) < MAX_ATTENDING and pending_offer is None:
            attending.append(user)
        else:
            standby.append(user)
        sync_ids_from_users()
        await interaction.response.send_message("Updated your attendance.", ephemeral=True)
        await self.update_embed()

    async def join_standby(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        if user in standby:
            await interaction.response.send_message("You're already on standby!", ephemeral=True)
            return
        if user in attending:
            attending.remove(user)
        if user in not_attending:
            not_attending.remove(user)
        standby.append(user)
        sync_ids_from_users()
        await interaction.response.send_message("Added to standby.", ephemeral=True)
        await self.update_embed()

    async def not_attend(self, interaction: discord.Interaction):
        if await self._handle_common_checks(interaction): return
        user = interaction.user
        removed = False
        if user in attending:
            attending.remove(user)
            removed = True
        elif user in standby:
            standby.remove(user)
            removed = True
        if removed:
            await offer_next_standby()
        if user not in not_attending:
            not_attending.append(user)
        sync_ids_from_users()
        await interaction.response.send_message("Marked as not attending.", ephemeral=True)
        await self.update_embed()

    async def relieve_spot(self, interaction: discord.Interaction):
        user = interaction.user
        if session_ended:
            await interaction.response.send_message("🔴 Session has ended.", ephemeral=True)
            return
        if user not in attending:
            await interaction.response.send_message("You are not in Attending.", ephemeral=True)
            return
        attending.remove(user)
        if user not in not_attending:
            not_attending.append(user)
        sync_ids_from_users()
        await offer_next_standby()
        await interaction.response.send_message(
            "You have relieved your spot. It has been offered to standby.", ephemeral=True
        )
        await self.update_embed()

    async def end_session_btn(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Only admins can end a session.", ephemeral=True)
            return
        if session_ended:
            await interaction.response.send_message("Session has already ended.", ephemeral=True)
            return
        await interaction.response.send_message("🔴 Ending session...", ephemeral=True)
        await end_session()

    async def menu_btn(self, interaction: discord.Interaction):
        """Whisper the help menu to the user (true ephemeral)."""
        user_is_admin = is_admin(interaction.user)
        embed = _build_everyone_embed()
        if user_is_admin:
            embed.set_footer(text="Page 1 · Only you can see this")
            admin_embed = _build_admin_embed()
            admin_embed.set_footer(text="Page 2 · Only you can see this")
            test_embed = _build_test_embed()
            test_embed.set_footer(text="Page 3 · Only you can see this")
            # Send all 3 pages in one ephemeral response for admins
            await interaction.response.send_message(
                embeds=[embed, admin_embed, test_embed],
                ephemeral=True
            )
        else:
            embed.set_footer(text="Only you can see this")
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

    async def leaderboard_btn(self, interaction: discord.Interaction):
        """Post the attendance leaderboard as an image in the channel (with 60s cooldown)."""
        global _leaderboard_cooldown
        now = datetime.now(EST)
        if _leaderboard_cooldown and (now - _leaderboard_cooldown).total_seconds() < 60:
            remaining = 60 - int((now - _leaderboard_cooldown).total_seconds())
            await interaction.response.send_message(
                f"⏳ Leaderboard was just posted. Try again in **{remaining}s**.",
                ephemeral=True
            )
            return

        if not attendance_history:
            await interaction.response.send_message("📊 No attendance data yet.", ephemeral=True)
            return

        guild = interaction.guild
        clicker_id = str(interaction.user.id)
        entries = []

        if session_type == 'nesting':
            for uid_str, data in attendance_history.items():
                parents = data.get("nest_parent_count", 0)
                babies = data.get("nest_baby_count", 0)
                protectors = data.get("nest_protector_count", 0)
                total_nest = parents + babies + protectors
                if total_nest == 0:
                    continue
                try:
                    member = guild.get_member(int(uid_str))
                    name = member.display_name if member else f"User {uid_str}"
                except:
                    name = f"User {uid_str}"
                entries.append((uid_str, name, parents, babies, protectors))

            # Sort by total nested roles played, then by parents as tie-breaker
            entries.sort(key=lambda x: ((x[2]+x[3]+x[4]), x[2]), reverse=True)
            entries = entries[:15]
            if not entries:
                await interaction.response.send_message("🥚 No nesting data yet.", ephemeral=True)
                return
            img_bytes = _render_nesting_leaderboard_image(entries, clicker_id)
        else:
            for uid_str, data in attendance_history.items():
                total = data.get("total_signups", 0)
                if total == 0:
                    continue
                attended = data.get("attended", 0)
                no_shows = data.get("no_shows", 0)
                streak = data.get("streak", 0)
                rate = (attended / total) * 100
                try:
                    member = guild.get_member(int(uid_str))
                    name = member.display_name if member else f"User {uid_str}"
                except:
                    name = f"User {uid_str}"
                entries.append((uid_str, name, attended, no_shows, rate, streak))

            entries.sort(key=lambda x: x[4], reverse=True)
            entries = entries[:15]  # top 15
            if not entries:
                await interaction.response.send_message("📊 No attendance data yet.", ephemeral=True)
                return
            img_bytes = _render_leaderboard_image(entries, clicker_id)

        file = discord.File(fp=img_bytes, filename="leaderboard.png")
        _leaderboard_cooldown = now
        await interaction.response.send_message(file=file)

# ----------------------------
# Create / Reset Session
# ----------------------------
async def create_schedule(channel, session_name_arg: str, session_dt: datetime = None, stype: str = 'hunt'):
    global attending, standby, not_attending, pending_offer, event_message, schedule_view
    global session_name, session_dt_str, event_message_id, event_channel_id
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id
    global reminder_sent, checkin_active, checked_in_ids, checkin_message_id
    global countdown_task, session_ended, session_type, nest_parent_ids, nest_baby_ids

    # Set session type
    session_type = stype if stype in SESSION_TYPES else 'hunt'
    nest_parent_ids = []
    nest_baby_ids = []

    # Archive the previous session if there was one
    if session_name and (attending_ids or standby_ids or not_attending_ids):
        if not session_ended:
            await end_session()
        else:
            await archive_session()

    # Cancel any running countdown timer
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
        countdown_task = None

    # Strip buttons from the old session message (preserve for chat history)
    if event_message:
        try:
            old_embed = event_message.embeds[0] if event_message.embeds else build_embed()
            old_embed.set_footer(text="🔴 Session closed — a new session has been created")
            old_embed.color = 0x95a5a6  # gray out
            await event_message.edit(embed=old_embed, view=None)
        except Exception:
            pass
        event_message = None
    elif event_message_id and event_channel_id:
        # Fallback: fetch and strip by stored ID (e.g. after bot restart)
        try:
            old_ch = await bot.fetch_channel(event_channel_id)
            old_msg = await old_ch.fetch_message(event_message_id)
            old_embed = old_msg.embeds[0] if old_msg.embeds else discord.Embed(title="Session Closed")
            old_embed.set_footer(text="🔴 Session closed — a new session has been created")
            old_embed.color = 0x95a5a6
            await old_msg.edit(embed=old_embed, view=None)
        except Exception:
            pass

    # Clear runtime lists
    attending.clear()
    standby.clear()
    not_attending.clear()
    pending_offer = None

    # Clear persisted IDs
    attending_ids = []
    standby_ids = []
    not_attending_ids = []
    pending_offer_id = None
    reminder_sent = False
    checkin_active = False
    checked_in_ids = []
    checkin_message_id = None
    session_ended = False

    # Store session info
    session_name = session_name_arg
    session_dt_str = session_dt.isoformat() if session_dt else None

    if session_dt:
        unix_ts = int(session_dt.timestamp())
        session_time_str = f"<t:{unix_ts}:f>"
        title = f"Session Sign-Up: {session_name_arg} ({session_time_str})"
    else:
        title = f"Session Sign-Up: {session_name_arg}"

    embed = discord.Embed(title=title, color=0x2ecc71)
    embed.add_field(name=f"Attending (0/{MAX_ATTENDING})", value="None", inline=False)
    embed.add_field(name="Standby (0)", value="None", inline=False)
    embed.add_field(name="Not Attending", value="None", inline=False)

    schedule_view = ScheduleView()
    try:
        event_message = await channel.send(content="@everyone", embed=embed, view=schedule_view)
    except discord.Forbidden:
        # Fallback if bot can't mention @everyone
        event_message = await channel.send(embed=embed, view=schedule_view)

    # Save message info for persistence
    event_message_id = event_message.id
    event_channel_id = channel.id
    save_state()

    # Start live countdown timer if session has a future datetime
    if session_dt:
        countdown_task = asyncio.create_task(_run_countdown())

    # Post to status channel if configured
    if status_channel_id:
        try:
            status_ch = await bot.fetch_channel(status_channel_id)
            msg = status_start_msg.replace('{name}', session_name_arg)
            status_embed = discord.Embed(
                title="Session Started 🟢",
                description=msg,
                color=0x43b581
            )
            if session_dt:
                unix_ts = int(session_dt.timestamp())
                status_embed.add_field(name="Time", value=f"<t:{unix_ts}:f>", inline=True)
            await status_ch.send(embed=status_embed)
        except Exception as e:
            print(f"❌ Could not post to status channel: {e}")

async def edit_current_session(new_name: str, new_dt_str: str):
    """Called by dashboard.py to instantly alter the active session name/time without creating a new message."""
    global session_name, session_dt_str, event_message, countdown_task
    
    session_name = new_name
    session_dt_str = new_dt_str
    save_state()
    
    # Reload the live countdown task with the new target time
    if countdown_task and not countdown_task.done():
        countdown_task.cancel()
    if session_dt_str:
        countdown_task = asyncio.create_task(_run_countdown())
        
    # Dynamically update the Discord message embed
    if event_message:
        try:
            await event_message.edit(embed=build_embed())
            print(f"✅ Session actively edited to: {new_name}")
        except Exception as e:
            print(f"❌ Failed to edit Discord message: {e}")

# ----------------------------
# Live Countdown Timer
# ----------------------------
async def _run_countdown():
    """Edits the session embed with a live countdown, then elapsed time after start."""
    global event_message, session_dt_str
    started = False
    session_online_posted = False
    try:
        while True:
            # Before start: update every 10s. After start: every 30s.
            await asyncio.sleep(10 if not started else 30)
            if not event_message or not session_dt_str:
                return
            if session_ended:
                return  # session was ended by admin
            try:
                session_dt = datetime.fromisoformat(session_dt_str)
                now = datetime.now(session_dt.tzinfo or EST)
                remaining = (session_dt - now).total_seconds()

                if remaining > 0:
                    # ── COUNTING DOWN ──
                    mins, secs = divmod(int(remaining), 60)
                    hours, mins = divmod(mins, 60)
                    if hours > 0:
                        countdown_str = f"{hours}h {mins}m {secs}s"
                    elif mins > 0:
                        countdown_str = f"{mins}m {secs}s"
                    else:
                        countdown_str = f"{secs}s"
                    if schedule_view:
                        embed = build_embed()
                        embed.set_footer(text=f"⏰ Starts in {countdown_str}")
                        await event_message.edit(embed=embed, view=schedule_view)
                else:
                    # ── SESSION STARTED — show elapsed time ──
                    if not started:
                        started = True
                        # Post "Session Online" to archive channel (once)
                        if not session_online_posted:
                            session_online_posted = True
                            try:
                                archive_ch = await bot.fetch_channel(archive_channel_id)
                                if archive_ch:
                                    sname = SESSION_TYPES.get(session_type, SESSION_TYPES['hunt'])['label']
                                    online_embed = discord.Embed(
                                        title=f"{sname} Online 🟢",
                                        description="We're live for OOTAH TIME!",
                                        color=0x2ecc71
                                    )
                                    await archive_ch.send(embed=online_embed)
                            except Exception as e:
                                print(f"❌ Could not post online message: {e}")

                    elapsed = int(abs(remaining))
                    mins, secs = divmod(elapsed, 60)
                    hours, mins = divmod(mins, 60)
                    if hours > 0:
                        elapsed_str = f"{hours}h {mins}m ago"
                    elif mins > 0:
                        elapsed_str = f"{mins}m ago"
                    else:
                        elapsed_str = f"{secs}s ago"
                    if schedule_view:
                        embed = build_embed()
                        embed.set_footer(text=f"⏰ Started {elapsed_str} · 🟢 Session has started!")
                        await event_message.edit(embed=embed, view=schedule_view)
                    # Auto-end after 4 hours post-start
                    if elapsed >= 14400:
                        await end_session()
                        return
            except discord.NotFound:
                return  # message was deleted
            except discord.HTTPException:
                await asyncio.sleep(5)  # back off on rate limit
    except asyncio.CancelledError:
        return

# ----------------------------
# Commands
# ----------------------------
@bot.command(help="Create a Beta Led session. Usage: !schedule [type] [hour] (e.g. !schedule nesting 20)")
async def schedule(ctx, *args):
    """Create a Beta Led Session. Role-gated to Beta and Lead Beta roles."""
    if not has_beta_role(ctx.author):
        await ctx.send("❌ Only users with the **Beta** or **Lead Beta** role can schedule sessions.", delete_after=10)
        return

    # Parse args: look for hour (int) and session type (string matching SESSION_TYPES)
    hour = None
    stype = 'hunt'
    for arg in args:
        try:
            hour = int(arg)
        except ValueError:
            lower = arg.lower()
            if lower in SESSION_TYPES:
                stype = lower
            # skip unrecognized words like 'beta', 'led'

    if hour is None:
        hour = 20  # default to 8 PM

    if hour < 0 or hour > 23:
        await ctx.send("❌ Hour must be 0–23 (24h format). Example: `!schedule nesting 20` for 8 PM.", delete_after=10)
        return

    # Build session datetime for today at the specified hour
    now = datetime.now(EST)
    session_dt = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    sinfo = SESSION_TYPES[stype]
    h12 = hour % 12 or 12
    ampm = "PM" if hour >= 12 else "AM"
    await create_schedule(ctx.channel, f"Beta Led {sinfo['label']}", session_dt=session_dt, stype=stype)
    await ctx.send(f"✅ {sinfo['emoji']} **Beta Led {sinfo['label']}** scheduled for **{h12}{ampm} EST** today!", delete_after=10)

@bot.command(help="Create a quick test session. Usage: !testsession [type] [minutes] (default 1 min, admin only)")
async def testsession(ctx, *args):
    """Create a quick test session. Admin only."""
    if not await check_admin(ctx):
        return

    minutes = 1
    stype = 'hunt'
    
    for arg in args:
        try:
            minutes = int(arg)
        except ValueError:
            lower = arg.lower()
            if lower in SESSION_TYPES:
                stype = lower

    if minutes < 1 or minutes > 120:
        await ctx.send("❌ Minutes must be between 1 and 120.", delete_after=5)
        return

    test_dt = datetime.now(EST) + timedelta(minutes=minutes)
    await create_schedule(ctx.channel, f"Test Session ({minutes}min)", session_dt=test_dt, stype=stype)

# ----------------------------
# Force next session
# ----------------------------
@bot.command(help="Force-post the next upcoming scheduled session.")
async def force(ctx):
    now = datetime.now(EST)
    next_session = None
    session_name_arg = None

    for sd in session_days:
        session_dt = next_run_time(sd["hour"], sd["weekday"])
        if session_dt >= now:
            next_session = session_dt
            session_name_arg = f"{sd['name']} {sd['hour'] % 12 or 12}{'AM' if sd['hour'] < 12 else 'PM'} EST Session"
            break

    if not next_session:
        sd = session_days[0]
        next_session = next_run_time(sd["hour"], sd["weekday"])
        session_name_arg = f"{sd['name']} {sd['hour'] % 12 or 12}{'AM' if sd['hour'] < 12 else 'PM'} EST Session"

    try:
        channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
        await create_schedule(channel, session_name_arg, session_dt=next_session)
        await ctx.send(f"✅ Forced creation of session: {session_name_arg}")
    except Exception as e:
        await ctx.send(f"❌ Failed to post session: {e}")

# ----------------------------
# Admin Commands
# ----------------------------
@bot.command(help="Set max attendees (1–50). Admin only. Usage: !setmax <n>")
async def setmax(ctx, n: int):
    if not await check_admin(ctx):
        return
    global MAX_ATTENDING
    if n < 1 or n > 50:
        await ctx.send("❌ Max must be between 1 and 50.")
        return
    MAX_ATTENDING = n
    save_state()
    await ctx.send(f"✅ Max attending set to **{n}**")
    if schedule_view:
        await schedule_view.update_embed()

@bot.command(help="Add a recurring session day. Admin only. Usage: !addday Thursday 20")
async def addday(ctx, weekday: str, hour: int):
    """Add a session day. Usage: !addday Thursday 20 (for 8PM)"""
    if not await check_admin(ctx):
        return
    day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    day_lower = weekday.lower()
    if day_lower not in day_map:
        await ctx.send(f"❌ Invalid day. Use: {', '.join(day_map.keys())}")
        return
    if hour < 0 or hour > 23:
        await ctx.send("❌ Hour must be 0-23 (24h format).")
        return

    wd = day_map[day_lower]
    # Check for duplicate
    for sd in session_days:
        if sd["weekday"] == wd and sd["hour"] == hour:
            await ctx.send("❌ That session day/time already exists.")
            return

    session_days.append({
        "weekday": wd,
        "hour": hour,
        "name": weekday.capitalize(),
        "post_hours_before": 20
    })
    save_state()
    h12 = hour % 12 or 12
    ampm = "AM" if hour < 12 else "PM"
    await ctx.send(f"✅ Added **{weekday.capitalize()} {h12}{ampm}** session.")

@bot.command(help="Remove a session day. Admin only. Usage: !removeday Thursday")
async def removeday(ctx, weekday: str):
    """Remove a session day. Usage: !removeday Thursday"""
    if not await check_admin(ctx):
        return
    day_map = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    day_lower = weekday.lower()
    if day_lower not in day_map:
        await ctx.send(f"❌ Invalid day. Use: {', '.join(day_map.keys())}")
        return

    wd = day_map[day_lower]
    before = len(session_days)
    session_days[:] = [sd for sd in session_days if sd["weekday"] != wd]
    if len(session_days) == before:
        await ctx.send(f"❌ No sessions on {weekday.capitalize()} to remove.")
        return
    save_state()
    await ctx.send(f"✅ Removed all sessions on **{weekday.capitalize()}**.")

@bot.command(help="Remove a user from all lists. Admin only. Usage: !kick @user")
async def kick(ctx, member: discord.Member):
    """Remove a user from all lists. Usage: !kick @user"""
    if not await check_admin(ctx):
        return
    removed_from = []
    if member in attending:
        attending.remove(member)
        removed_from.append("attending")
    if member in standby:
        standby.remove(member)
        removed_from.append("standby")
    if member in not_attending:
        not_attending.remove(member)
        removed_from.append("not attending")

    if removed_from:
        sync_ids_from_users()
        await offer_next_standby()
        if schedule_view:
            await schedule_view.update_embed()
        await ctx.send(f"✅ Removed {member.mention} from: {', '.join(removed_from)}")
    else:
        await ctx.send(f"❌ {member.mention} is not in any list.")

@bot.command(help="Reset a user's attendance stats. Owner only. Usage: !resetstats @user")
async def resetstats(ctx, member: discord.Member):
    """Reset a user's attendance stats. Usage: !resetstats @user"""
    if ctx.author.id not in OWNER_IDS:
        await ctx.send("❌ Only bot owners can reset stats.", delete_after=5)
        return
    key = str(member.id)
    if key in attendance_history:
        attendance_history[key] = {
            "attended": 0, "no_shows": 0, "total_signups": 0,
            "streak": 0, "best_streak": 0
        }
        save_history()
        await ctx.send(f"✅ Reset stats for {member.mention}")
    else:
        await ctx.send(f"❌ No stats found for {member.mention}")

@bot.command(help="Set check-in grace period (5–120 min). Admin only. Usage: !setgrace 30")
async def setgrace(ctx, minutes: int):
    """Set check-in grace period. Usage: !setgrace 30"""
    if not await check_admin(ctx):
        return
    global CHECKIN_GRACE_MINUTES
    if minutes < 5 or minutes > 120:
        await ctx.send("❌ Grace period must be between 5 and 120 minutes.")
        return
    CHECKIN_GRACE_MINUTES = minutes
    save_state()
    await ctx.send(f"✅ Check-in grace period set to **{minutes} minutes**")

@bot.command(help="Set no-show threshold for auto-standby. Admin only. Usage: !setnoshow 3")
async def setnoshow(ctx, n: int):
    """Set no-show threshold for auto-standby. Usage: !setnoshow 3"""
    if not await check_admin(ctx):
        return
    global NOSHOW_THRESHOLD
    if n < 1 or n > 20:
        await ctx.send("❌ Threshold must be between 1 and 20.")
        return
    NOSHOW_THRESHOLD = n
    save_state()
    await ctx.send(f"✅ No-show threshold set to **{n}** (auto-standby after {n} no-shows)")

@bot.command(help="Show current bot settings. Admin only.")
async def settings(ctx):
    """Show current bot settings"""
    if not await check_admin(ctx):
        return
    days_list = ", ".join(sd['name'] for sd in sorted(session_days, key=lambda x: x['weekday'])) or "None"
    roles_list = ", ".join(admin_role_names) or "None"
    beta_list = ", ".join(beta_role_names) or "None"
    status = "🔴 Ended" if session_ended else ("🟢 Active" if session_has_started() else ("⏳ Scheduled" if session_dt_str else "—"))
    embed = discord.Embed(title="⚙️ Bot Settings", color=0x95a5a6)
    embed.add_field(name="Max Attending", value=str(MAX_ATTENDING), inline=True)
    embed.add_field(name="Check-In Grace", value=f"{CHECKIN_GRACE_MINUTES} min", inline=True)
    embed.add_field(name="No-Show Threshold", value=f"{NOSHOW_THRESHOLD} (auto-standby)", inline=True)
    embed.add_field(name="Session Days", value=days_list, inline=False)
    embed.add_field(name="Admin Roles", value=roles_list, inline=True)
    embed.add_field(name="Beta Roles", value=beta_list, inline=True)
    embed.add_field(name="Archive Channel", value=f"<#{archive_channel_id}>", inline=True)
    embed.add_field(name="Session Status", value=status, inline=True)
    sinfo = SESSION_TYPES.get(session_type, SESSION_TYPES['hunt'])
    embed.add_field(name="Session Type", value=f"{sinfo['emoji']} {sinfo['label']}", inline=True)
    embed.add_field(name="Owner Admin", value=f"<@{ADMIN_ID}>", inline=True)
    await ctx.send(embed=embed)

@bot.command(help="Set admin roles. Admin only. Usage: !setadminroles Admin Moderator")
async def setadminroles(ctx, *roles):
    """Set which role names grant admin access. Usage: !setadminroles Admin Moderator"""
    if not await check_admin(ctx):
        return
    global admin_role_names
    if not roles:
        await ctx.send(f"Current admin roles: **{', '.join(admin_role_names)}**\nUsage: `!setadminroles RoleName1 RoleName2`")
        return
    admin_role_names = list(roles)
    save_state()
    await ctx.send(f"✅ Admin roles set to: **{', '.join(admin_role_names)}**")

@bot.command(help="Set beta scheduling roles. Admin only. Usage: !setbetaroles Beta 'Lead Beta'")
async def setbetaroles(ctx, *roles):
    """Set which role names can use !schedule. Usage: !setbetaroles Beta 'Lead Beta'"""
    if not await check_admin(ctx):
        return
    global beta_role_names
    if not roles:
        await ctx.send(f"Current beta roles: **{', '.join(beta_role_names)}**\nUsage: `!setbetaroles RoleName1 RoleName2`")
        return
    beta_role_names = list(roles)
    save_state()
    await ctx.send(f"✅ Beta scheduling roles set to: **{', '.join(beta_role_names)}**")

@bot.command(help="Set the archive channel. Admin only. Usage: !setarchivechannel #channel")
async def setarchivechannel(ctx, channel: discord.TextChannel):
    """Set which channel session archives are posted to. Usage: !setarchivechannel #channel"""
    if not await check_admin(ctx):
        return
    global archive_channel_id
    archive_channel_id = channel.id
    save_state()
    await ctx.send(f"✅ Archive channel set to {channel.mention}")

@bot.command(help="End the current session manually. Admin only.")
async def endsession(ctx):
    """End the current session, archive it, and post offline message."""
    if not await check_admin(ctx):
        return
    if session_ended:
        await ctx.send("❌ No active session to end.", delete_after=5)
        return
    if not session_dt_str:
        await ctx.send("❌ No session is currently scheduled.", delete_after=5)
        return
    await ctx.send("🔴 Ending session...")
    await end_session()

# ----------------------------
# Session Type & Nesting Commands
# ----------------------------
@bot.command(help="Set session type. Usage: !settype hunt|nesting|growth|pvp|migration")
async def settype(ctx, type_name: str = None):
    """Change the current session's type."""
    if not has_beta_role(ctx.author) and not await check_admin(ctx):
        return
    if not type_name or type_name.lower() not in SESSION_TYPES:
        types_list = ', '.join(f"`{k}` {v['emoji']}" for k, v in SESSION_TYPES.items())
        await ctx.send(f"❌ Invalid type. Available: {types_list}", delete_after=10)
        return

    global session_type, nest_parent_ids, nest_baby_ids, nest_protector_ids
    session_type = type_name.lower()
    if session_type != 'nesting':
        nest_parent_ids = []
        nest_baby_ids = []
        nest_protector_ids = []
    save_state()

    sinfo = SESSION_TYPES[session_type]
    await ctx.send(f"{sinfo['emoji']} Session type changed to **{sinfo['label']}**!", delete_after=10)

    # Update the embed
    if schedule_view and event_message:
        await schedule_view.update_embed()

@bot.command(help="Designate a user as nest parent. Usage: !parent @user")
async def parent(ctx, member: discord.Member):
    """Designate a user as a nest parent (nesting sessions only)."""
    if not has_beta_role(ctx.author) and not await check_admin(ctx):
        return
    if session_type != 'nesting':
        await ctx.send("❌ This command only works in **Nesting** sessions. Use `!settype nesting` first.", delete_after=10)
        return
    global nest_parent_ids
    if member.id not in nest_parent_ids:
        nest_parent_ids.append(member.id)
        # Also ensure they're in the attending list
        if member not in attending and member.id not in attending_ids:
            attending.append(member)
            attending_ids.append(member.id)
        save_state()
    await ctx.send(f"🦕 {member.mention} is now a **Nest Parent**!", delete_after=10)
    if schedule_view and event_message:
        await schedule_view.update_embed()

@bot.command(help="Remove parent designation. Usage: !unparent @user")
async def unparent(ctx, member: discord.Member):
    """Remove nest parent designation."""
    if not has_beta_role(ctx.author) and not await check_admin(ctx):
        return
    global nest_parent_ids
    if member.id in nest_parent_ids:
        nest_parent_ids.remove(member.id)
        save_state()
        await ctx.send(f"✅ {member.mention} is no longer a parent.", delete_after=10)
        if schedule_view and event_message:
            await schedule_view.update_embed()
    else:
        await ctx.send(f"❌ {member.mention} is not a parent.", delete_after=10)

@bot.command(help="Designate a user as a baby. Usage: !baby @user")
async def baby(ctx, member: discord.Member):
    """Designate a user as a baby in a nesting session."""
    if not has_beta_role(ctx.author) and not await check_admin(ctx):
        return
    if session_type != 'nesting':
        await ctx.send("❌ This command only works in **Nesting** sessions. Use `!settype nesting` first.", delete_after=10)
        return
    global nest_baby_ids
    if member.id not in nest_baby_ids:
        nest_baby_ids.append(member.id)
        # Also ensure they're in the attending list
        if member not in attending and member.id not in attending_ids:
            attending.append(member)
            attending_ids.append(member.id)
        # Remove from parent list if they were there
        if member.id in nest_parent_ids:
            nest_parent_ids.remove(member.id)
        save_state()
    await ctx.send(f"🐣 {member.mention} is now a **Baby**!", delete_after=10)
    if schedule_view and event_message:
        await schedule_view.update_embed()

@bot.command(help="Remove baby designation. Usage: !unbaby @user")
async def unbaby(ctx, member: discord.Member):
    """Remove baby designation."""
    if not has_beta_role(ctx.author) and not await check_admin(ctx):
        return
    global nest_baby_ids
    if member.id in nest_baby_ids:
        nest_baby_ids.remove(member.id)
        save_state()
        await ctx.send(f"✅ {member.mention} is no longer a baby.", delete_after=10)
        if schedule_view and event_message:
            await schedule_view.update_embed()
    else:
        await ctx.send(f"❌ {member.mention} is not a baby.", delete_after=10)

@bot.command(help="Show current nesting status. Everyone can use this.")
async def nest(ctx):
    """Show current nesting status."""
    if session_type != 'nesting':
        await ctx.send("❌ No active nesting session. Current type: **" + SESSION_TYPES.get(session_type, {}).get('label', 'Unknown') + "**", delete_after=10)
        return

    parents = [u for u in attending if u.id in nest_parent_ids]
    babies = [u for u in attending if u.id in nest_baby_ids]
    protectors = [u for u in attending if u.id not in nest_parent_ids and u.id not in nest_baby_ids]

    embed = discord.Embed(title="🥚 Nesting Status", color=0xf1c40f)
    embed.add_field(
        name=f"🦕 Parents ({len(parents)})",
        value="\n".join(u.mention for u in parents) if parents else "*None*",
        inline=True
    )
    embed.add_field(
        name=f"🐣 Babies ({len(babies)})",
        value="\n".join(u.mention for u in babies) if babies else "*None*",
        inline=True
    )
    embed.add_field(
        name=f"🛡️ Protectors ({len(protectors)})",
        value="\n".join(u.mention for u in protectors) if protectors else "*None*",
        inline=True
    )
    await ctx.send(embed=embed)

# ----------------------------
# Stats / Leaderboard
# ----------------------------
@bot.command(help="Show the attendance leaderboard. Usage: !stats or !stats nesting")
async def stats(ctx, stype: str = None):
    """Show the attendance leaderboard"""
    if not attendance_history:
        await ctx.send("📊 No data yet.")
        return

    # Check for nesting stats request
    if stype and stype.lower() == 'nesting':
        entries = []
        guild = ctx.guild
        for uid_str, data in attendance_history.items():
            parents = data.get("nest_parent_count", 0)
            babies = data.get("nest_baby_count", 0)
            protectors = data.get("nest_protector_count", 0)
            total = parents + babies + protectors
            if total == 0:
                continue
            try:
                member = guild.get_member(int(uid_str))
                name = member.display_name if member else f"User {uid_str}"
            except:
                name = f"User {uid_str}"
            entries.append((name, parents, babies, protectors, total))

        entries.sort(key=lambda x: x[4], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (name, parents, babies, protectors, total) in enumerate(entries[:15]):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} **{name}** — 🦕 {parents} | 🐣 {babies} | 🛡️ {protectors}")

        if not lines:
            await ctx.send("🥚 No nesting data yet.")
            return

        embed = discord.Embed(title="🥚 Nesting Leaderboard", description="\n".join(lines), color=0xf1c40f)
        await ctx.send(embed=embed)
        return

    # Sort by standard attendance rate
    entries = []
    guild = ctx.guild
    for uid_str, data in attendance_history.items():
        total = data.get("total_signups", 0)
        if total == 0:
            continue
        attended = data.get("attended", 0)
        no_shows = data.get("no_shows", 0)
        streak = data.get("streak", 0)
        rate = (attended / total) * 100
        try:
            member = guild.get_member(int(uid_str))
            name = member.display_name if member else f"User {uid_str}"
        except:
            name = f"User {uid_str}"

        entries.append((name, attended, total, rate, streak, no_shows))

    entries.sort(key=lambda x: x[3], reverse=True)  # sort by rate

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, attended, total, rate, streak, no_shows) in enumerate(entries[:15]):
        medal = medals[i] if i < 3 else f"{i+1}."
        streak_str = f" 🔥{streak}" if streak >= 3 else ""
        noshow_str = f" ⚠️{no_shows}NS" if no_shows > 0 else ""
        lines.append(f"{medal} **{name}** — {attended}/{total} ({rate:.0f}%){streak_str}{noshow_str}")

    if not lines:
        await ctx.send("📊 No standard attendance data yet.")
        return

    embed = discord.Embed(title="📊 Attendance Leaderboard", description="\n".join(lines), color=0x3498db)
    await ctx.send(embed=embed)

@bot.command(help="View your personal attendance stats (private).")
async def mystats(ctx):
    """Show your own attendance stats (private)"""
    stats = get_user_stats(ctx.author.id)
    total = stats["total_signups"]
    rate = (stats["attended"] / total * 100) if total > 0 else 0
    embed = discord.Embed(title=f"📊 Your Stats", color=0x9b59b6)
    embed.add_field(name="Sessions Attended", value=str(stats["attended"]), inline=True)
    embed.add_field(name="No-Shows", value=str(stats["no_shows"]), inline=True)
    embed.add_field(name="Total Sign-Ups", value=str(stats["total_signups"]), inline=True)
    embed.add_field(name="Attendance Rate", value=f"{rate:.0f}%", inline=True)
    embed.add_field(name="Current Streak", value=f"{'🔥' if stats['streak'] >= 3 else ''}{stats['streak']}", inline=True)
    embed.add_field(name="Best Streak", value=str(stats["best_streak"]), inline=True)

    if is_auto_standby(ctx.author.id):
        embed.set_footer(text=f"⚠️ You have {stats['no_shows']} no-shows — auto-placed on standby until improved.")

    await ctx.send(embed=embed, ephemeral=True)

# ----------------------------
# Swap command
# ----------------------------
@bot.command(help="Request to swap spots with another user. Usage: !swap @user")
async def swap(ctx, target: discord.Member):
    """Request to swap spots with another user. Usage: !swap @user"""
    requester = ctx.author
    if requester == target:
        await ctx.send("❌ You can't swap with yourself.", delete_after=5)
        return

    # Verify both are in some list
    req_in = requester in attending or requester in standby
    tgt_in = target in attending or target in standby
    if not req_in or not tgt_in:
        await ctx.send("❌ Both users must be on attending or standby to swap.", delete_after=5)
        return

    # Same list = no point
    if (requester in attending and target in attending) or (requester in standby and target in standby):
        await ctx.send("❌ You're both in the same list — nothing to swap.", delete_after=5)
        return

    try:
        await target.send(
            f"🔄 **{requester.display_name}** wants to swap spots with you!\n"
            f"They are {'attending' if requester in attending else 'on standby'}, "
            f"you are {'attending' if target in attending else 'on standby'}.",
            view=SwapView(requester.id, target.id)
        )
        await ctx.send(f"✅ Swap request sent to {target.mention}!", delete_after=10)
    except:
        await ctx.send(f"❌ Couldn't DM {target.mention}. They may have DMs disabled.", delete_after=10)

# ----------------------------
# Session days display
# ----------------------------
@bot.command(help="Show the configured session schedule.")
async def days(ctx):
    """Show configured session days"""
    if not session_days:
        await ctx.send("📅 No session days configured.")
        return
    lines = []
    for sd in sorted(session_days, key=lambda x: x["weekday"]):
        h = sd["hour"]
        h12 = h % 12 or 12
        ampm = "AM" if h < 12 else "PM"
        lines.append(f"• **{sd['name']}** at {h12}{ampm} EST (posts {sd['post_hours_before']}h before)")
    embed = discord.Embed(title="📅 Session Schedule", description="\n".join(lines), color=0xe67e22)
    await ctx.send(embed=embed)

# ----------------------------
# Dino Battle Minigame
# ----------------------------
DINOS_FILE = os.path.join(os.path.dirname(__file__), "dinos.json")
DINO_LB_FILE = os.path.join(os.path.dirname(__file__), "dino_lb.json")
DINO_STATS_FILE = os.path.join(os.path.dirname(__file__), "dino_battle_stats.json")

DINO_TEMPLATES = [
    # Officials
    {"id": "t_rex", "name": "T-Rex", "type": "carnivore", "cw": 6500, "hp": 800, "atk": 80, "armor": 1.0, "spd": 800},
    {"id": "triceratops", "name": "Eotriceratops", "type": "herbivore", "cw": 7000, "hp": 850, "atk": 75, "armor": 1.25, "spd": 700},
    {"id": "velociraptor", "name": "Latenivenatrix", "type": "carnivore", "cw": 1000, "hp": 250, "atk": 35, "armor": 1.0, "spd": 1100},
    {"id": "stegosaurus", "name": "Stegosaurus", "type": "herbivore", "cw": 5500, "hp": 750, "atk": 70, "armor": 1.5, "spd": 600},
    {"id": "spinosaurus", "name": "Spinosaurus", "type": "carnivore", "cw": 6500, "hp": 850, "atk": 85, "armor": 1.0, "spd": 700},
    {"id": "megalania", "name": "Megalania", "type": "carnivore", "cw": 2800, "hp": 450, "atk": 50, "armor": 1.0, "spd": 850},
    
    # Modded
    {"id": "argentinosaurus", "name": "Argentinosaurus (Mod)", "type": "herbivore", "cw": 25000, "hp": 3000, "atk": 250, "armor": 1.5, "spd": 400},
    {"id": "deinosuchus", "name": "Deinosuchus (Mod)", "type": "carnivore", "cw": 8000, "hp": 900, "atk": 110, "armor": 1.1, "spd": 650},
    {"id": "utahraptor", "name": "Utahraptor (Mod)", "type": "carnivore", "cw": 1800, "hp": 350, "atk": 45, "armor": 1.0, "spd": 1050},
    {"id": "therizinosaurus", "name": "Therizinosaurus (Mod)", "type": "herbivore", "cw": 6000, "hp": 700, "atk": 120, "armor": 1.0, "spd": 850},
    {"id": "carcharodontosaurus", "name": "Carcharodontosaurus (Mod)", "type": "carnivore", "cw": 6300, "hp": 750, "atk": 90, "armor": 1.0, "spd": 820}
]

def load_dinos():
    if os.path.exists(DINOS_FILE):
        try:
            with open(DINOS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading {DINOS_FILE}: {e}")
    return DINO_TEMPLATES.copy()

def save_dinos(dinos_list):
    try:
        with open(DINOS_FILE, 'w') as f:
            json.dump(dinos_list, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving {DINOS_FILE}: {e}")

def load_dino_lb():
    if os.path.exists(DINO_LB_FILE):
        try:
            with open(DINO_LB_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading {DINO_LB_FILE}: {e}")
    return {}

def save_dino_lb(lb_data):
    try:
        with open(DINO_LB_FILE, 'w') as f:
            json.dump(lb_data, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving {DINO_LB_FILE}: {e}")

def load_dino_stats():
    if os.path.exists(DINO_STATS_FILE):
        try:
            with open(DINO_STATS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"❌ Error loading {DINO_STATS_FILE}: {e}")
    return {}

def save_dino_stats(stats):
    try:
        with open(DINO_STATS_FILE, 'w') as f:
            json.dump(stats, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving {DINO_STATS_FILE}: {e}")

def _record_dino_battle(result, dino_a, dino_b):
    """Record per-dino battle stats from engine result."""
    stats = load_dino_stats()
    fa = result["fighter_a"]
    fb = result["fighter_b"]
    
    for dino, fighter, side_label in [(dino_a, fa, "a"), (dino_b, fb, "b")]:
        did = dino.get("id", dino.get("name", "unknown"))
        if did not in stats:
            stats[did] = {
                "name": dino.get("name", did),
                "type": dino.get("type", "unknown"),
                "cw": dino.get("cw", 0),
                "wins": 0, "losses": 0, "ties": 0,
                "kills": 0, "deaths": 0, "flees": 0,
                "total_battles": 0,
                "battle_log": []
            }
        entry = stats[did]
        entry["total_battles"] += 1
        
        # Win/loss
        if result["winner"] == side_label:
            entry["wins"] += 1
        elif result["winner"] is None:
            entry["ties"] += 1
        else:
            entry["losses"] += 1
        
        # Deaths (pack members that died)
        dead = fighter.get("pack_size", 1) - fighter.get("alive_count", 1)
        entry["deaths"] += max(0, dead)
        
        # Flees
        entry["flees"] += fighter.get("fled_count", 0)
        
        # Kills (opponent's deaths)
        opp = fb if side_label == "a" else fa
        opp_dead = opp.get("pack_size", 1) - opp.get("alive_count", 1)
        entry["kills"] += max(0, opp_dead)
        
        # Battle log (last 20)
        opp_dino = dino_b if side_label == "a" else dino_a
        log_entry = {
            "vs": opp_dino.get("name", "Unknown"),
            "result": "win" if result["winner"] == side_label else ("tie" if result["winner"] is None else "loss"),
            "hp_left": fighter.get("hp", 0),
            "hp_max": fighter.get("max_hp", 0),
            "timestamp": datetime.now(EST).isoformat()
        }
        entry["battle_log"] = entry.get("battle_log", [])[-19:] + [log_entry]
    
    save_dino_stats(stats)

class DinoBattleView(discord.ui.View):
    def __init__(self, dino_a, dino_b):
        super().__init__(timeout=60) # Bets close in 60 seconds
        self.dino_a = dino_a
        self.dino_b = dino_b
        # Winner bets
        self.bets_a = set()
        self.bets_b = set()
        self.bets_tie = set()
        # Prop bets
        self.prop_flee_yes = set()
        self.prop_flee_no = set()
        self.prop_bleed_kill = set()
        self.prop_first_crit_a = set()
        self.prop_first_crit_b = set()
        self.prop_ko_over = set()   # 2+ KOs
        self.prop_ko_under = set()  # 0-1 KOs
        
        # Row 0: Winner bets
        btn_a = discord.ui.Button(label=f"🏆 {dino_a['name']}", style=discord.ButtonStyle.danger if dino_a['type'] == 'carnivore' else discord.ButtonStyle.success, custom_id="bet_a", row=0)
        btn_a.callback = self.bet_a_callback
        self.add_item(btn_a)

        btn_tie = discord.ui.Button(label="⚖️ Tie", style=discord.ButtonStyle.secondary, custom_id="bet_tie", row=0)
        btn_tie.callback = self.bet_tie_callback
        self.add_item(btn_tie)

        btn_b = discord.ui.Button(label=f"🏆 {dino_b['name']}", style=discord.ButtonStyle.danger if dino_b['type'] == 'carnivore' else discord.ButtonStyle.success, custom_id="bet_b", row=0)
        btn_b.callback = self.bet_b_callback
        self.add_item(btn_b)

        # Row 1: Prop bets — Flee & Bleed Kill
        btn_flee_yes = discord.ui.Button(label="🏃 Flee: Yes", style=discord.ButtonStyle.primary, custom_id="prop_flee_yes", row=1)
        btn_flee_yes.callback = self.prop_flee_yes_cb
        self.add_item(btn_flee_yes)

        btn_flee_no = discord.ui.Button(label="🏃 Flee: No", style=discord.ButtonStyle.secondary, custom_id="prop_flee_no", row=1)
        btn_flee_no.callback = self.prop_flee_no_cb
        self.add_item(btn_flee_no)

        btn_bleed = discord.ui.Button(label="🩸 Bleed Kill", style=discord.ButtonStyle.danger, custom_id="prop_bleed", row=1)
        btn_bleed.callback = self.prop_bleed_kill_cb
        self.add_item(btn_bleed)

        # Row 2: Prop bets — First Crit & KO Count
        btn_crit_a = discord.ui.Button(label=f"⚡ 1st Crit: {dino_a['name'][:10]}", style=discord.ButtonStyle.primary, custom_id="prop_crit_a", row=2)
        btn_crit_a.callback = self.prop_first_crit_a_cb
        self.add_item(btn_crit_a)

        btn_crit_b = discord.ui.Button(label=f"⚡ 1st Crit: {dino_b['name'][:10]}", style=discord.ButtonStyle.primary, custom_id="prop_crit_b", row=2)
        btn_crit_b.callback = self.prop_first_crit_b_cb
        self.add_item(btn_crit_b)

        btn_ko_over = discord.ui.Button(label="💀 KOs: 2+", style=discord.ButtonStyle.danger, custom_id="prop_ko_over", row=2)
        btn_ko_over.callback = self.prop_ko_over_cb
        self.add_item(btn_ko_over)

        btn_ko_under = discord.ui.Button(label="💀 KOs: 0-1", style=discord.ButtonStyle.secondary, custom_id="prop_ko_under", row=2)
        btn_ko_under.callback = self.prop_ko_under_cb
        self.add_item(btn_ko_under)

        # Row 3: LB & Menu
        btn_lb = discord.ui.Button(label="🏆 Leaderboard", style=discord.ButtonStyle.primary, custom_id="bet_lb", row=3)
        btn_lb.callback = self.lb_callback
        self.add_item(btn_lb)

        btn_menu = discord.ui.Button(label="📚 Menu", style=discord.ButtonStyle.primary, custom_id="bet_menu", row=3)
        btn_menu.callback = self.menu_callback
        self.add_item(btn_menu)

    async def _handle_bet(self, interaction: discord.Interaction, target_set, other_sets, bet_name):
        user_id = interaction.user.id
        for s in other_sets:
            if user_id in s:
                s.remove(user_id)
        if user_id in target_set:
            await interaction.response.send_message(f"You already bet on **{bet_name}**!", ephemeral=True)
            return
        target_set.add(user_id)
        await interaction.response.send_message(f"🎟️ Locked in: **{bet_name}**!", ephemeral=True)

    async def _handle_prop(self, interaction, target_set, other_set, prop_name):
        user_id = interaction.user.id
        if user_id in other_set:
            other_set.remove(user_id)
        if user_id in target_set:
            await interaction.response.send_message(f"You already bet **{prop_name}**!", ephemeral=True)
            return
        target_set.add(user_id)
        await interaction.response.send_message(f"🎲 Prop bet locked: **{prop_name}**!", ephemeral=True)

    # Winner bets
    async def bet_a_callback(self, interaction): await self._handle_bet(interaction, self.bets_a, [self.bets_b, self.bets_tie], self.dino_a['name'])
    async def bet_tie_callback(self, interaction): await self._handle_bet(interaction, self.bets_tie, [self.bets_a, self.bets_b], "Tie")
    async def bet_b_callback(self, interaction): await self._handle_bet(interaction, self.bets_b, [self.bets_a, self.bets_tie], self.dino_b['name'])

    # Prop bets
    async def prop_flee_yes_cb(self, interaction): await self._handle_prop(interaction, self.prop_flee_yes, self.prop_flee_no, "🏃 Flee: Yes")
    async def prop_flee_no_cb(self, interaction): await self._handle_prop(interaction, self.prop_flee_no, self.prop_flee_yes, "🏃 Flee: No")
    async def prop_bleed_kill_cb(self, interaction): await self._handle_prop(interaction, self.prop_bleed_kill, set(), "🩸 Bleed Kill")
    async def prop_first_crit_a_cb(self, interaction): await self._handle_prop(interaction, self.prop_first_crit_a, self.prop_first_crit_b, f"⚡ 1st Crit: {self.dino_a['name']}")
    async def prop_first_crit_b_cb(self, interaction): await self._handle_prop(interaction, self.prop_first_crit_b, self.prop_first_crit_a, f"⚡ 1st Crit: {self.dino_b['name']}")
    async def prop_ko_over_cb(self, interaction): await self._handle_prop(interaction, self.prop_ko_over, self.prop_ko_under, "💀 KOs: 2+")
    async def prop_ko_under_cb(self, interaction): await self._handle_prop(interaction, self.prop_ko_under, self.prop_ko_over, "💀 KOs: 0-1")

    async def lb_callback(self, interaction: discord.Interaction):
        lb = load_dino_lb()
        if not lb:
            await interaction.response.send_message("No Dino Battle bets on record yet!", ephemeral=True)
            return
        embed, file = await _build_dino_lb_embed(interaction.client, lb)
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    async def dino_lb_callback(self, interaction: discord.Interaction):
        stats = load_dino_stats()
        if not stats:
            await interaction.response.send_message("No dino battles recorded yet!", ephemeral=True)
            return
        embed, file = _build_dino_stats_embed(stats)
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    async def menu_callback(self, interaction: discord.Interaction):
        is_admin = False
        view = HelpView(interaction.user.id, show_admin=is_admin)
        embed = _build_everyone_embed()
        embed.set_footer(text="Session buttons: Attend · Standby · Not Attending · Relieve Spot")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class DinoPostBattleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        btn_again = discord.ui.Button(label="⚔️ Battle Again", style=discord.ButtonStyle.danger, custom_id="post_again")
        btn_again.callback = self.again_callback
        self.add_item(btn_again)
        btn_lb = discord.ui.Button(label="🏆 Leaderboard", style=discord.ButtonStyle.primary, custom_id="post_lb")
        btn_lb.callback = self.lb_callback
        self.add_item(btn_lb)
        btn_dino_lb = discord.ui.Button(label="🦕 Dino Leaderboard", style=discord.ButtonStyle.success, custom_id="post_dino_lb")
        btn_dino_lb.callback = self.dino_lb_callback
        self.add_item(btn_dino_lb)
        btn_menu = discord.ui.Button(label="📚 Menu", style=discord.ButtonStyle.primary, custom_id="post_menu")
        btn_menu.callback = self.menu_callback
        self.add_item(btn_menu)

    async def again_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message("⚔️ Starting a new battle! Use `!dinobattle` to join!", ephemeral=False)
        ctx = await bot.get_context(interaction.message)
        await dinobattle(ctx)

    async def lb_callback(self, interaction: discord.Interaction):
        lb = load_dino_lb()
        if not lb:
            await interaction.response.send_message("No Dino Battle bets on record yet!", ephemeral=True)
            return
        embed, file = await _build_dino_lb_embed(interaction.client, lb)
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    async def dino_lb_callback(self, interaction: discord.Interaction):
        stats = load_dino_stats()
        if not stats:
            await interaction.response.send_message("No dino battles recorded yet!", ephemeral=True)
            return
        embed, file = _build_dino_stats_embed(stats)
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)

    async def menu_callback(self, interaction: discord.Interaction):
        view = HelpView(interaction.user.id, show_admin=False)
        embed = _build_everyone_embed()
        embed.set_footer(text="Session buttons: Attend · Standby · Not Attending · Relieve Spot")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ---- Weekly Champion & Enhanced Leaderboard ----
def _get_week_key():
    """Return the ISO week key 'YYYY-WNN' for the current week."""
    now = datetime.now(EST)
    return now.strftime("%G-W%V")

def _ensure_lb_fields(entry):
    """Ensure a leaderboard entry has all required fields."""
    entry.setdefault("wins", 0)
    entry.setdefault("losses", 0)
    entry.setdefault("ties", 0)
    entry.setdefault("streak", 0)
    entry.setdefault("best_streak", 0)
    entry.setdefault("prop_wins", 0)
    entry.setdefault("prop_losses", 0)
    entry.setdefault("weekly", {})
    entry.setdefault("champion_stars", 0)
    return entry

def _record_weekly_win(lb, user_id_str):
    """Increment this user's wins for the current week."""
    entry = _ensure_lb_fields(lb.setdefault(user_id_str, {}))
    week = _get_week_key()
    if week not in entry["weekly"]:
        entry["weekly"][week] = {"wins": 0, "losses": 0, "prop_wins": 0}
    entry["weekly"][week]["wins"] += 1

def _record_weekly_loss(lb, user_id_str):
    entry = _ensure_lb_fields(lb.setdefault(user_id_str, {}))
    week = _get_week_key()
    if week not in entry["weekly"]:
        entry["weekly"][week] = {"wins": 0, "losses": 0, "prop_wins": 0}
    entry["weekly"][week]["losses"] += 1

def _record_weekly_prop(lb, user_id_str):
    entry = _ensure_lb_fields(lb.setdefault(user_id_str, {}))
    week = _get_week_key()
    if week not in entry["weekly"]:
        entry["weekly"][week] = {"wins": 0, "losses": 0, "prop_wins": 0}
    entry["weekly"][week]["prop_wins"] += 1

def _check_award_champion(lb):
    """Check previous week and award ⭐ to the top winner if not already awarded."""
    now = datetime.now(EST)
    # Get previous week key
    prev = now - timedelta(days=7)
    prev_week = prev.strftime("%G-W%V")
    current_week = _get_week_key()
    if prev_week == current_week:
        return None  # Same week, skip

    # Find the top winner for previous week
    best_uid = None
    best_wins = 0
    for uid, entry in lb.items():
        _ensure_lb_fields(entry)
        wk = entry.get("weekly", {}).get(prev_week, {})
        w = wk.get("wins", 0)
        if w > best_wins:
            best_wins = w
            best_uid = uid

    if best_uid and best_wins > 0:
        # Check if already awarded this week
        entry = lb[best_uid]
        awarded_weeks = entry.get("_awarded_weeks", [])
        if prev_week not in awarded_weeks:
            entry["champion_stars"] = entry.get("champion_stars", 0) + 1
            entry.setdefault("_awarded_weeks", []).append(prev_week)
            return best_uid
    return None


async def _build_dino_lb_embed(client, lb):
    """Build an enhanced leaderboard as an image, matching attendance LB style."""
    week = _get_week_key()
    champion_uid = _check_award_champion(lb)
    save_dino_lb(lb)

    sorted_lb = sorted(lb.items(), key=lambda x: x[1].get('wins', 0), reverse=True)
    
    # Resolve names
    entries = []
    for uid_str, stats in sorted_lb[:15]:
        _ensure_lb_fields(stats)
        try:
            user = await client.fetch_user(int(uid_str))
            name = user.display_name if user else f"User {uid_str}"
        except:
            name = f"User {uid_str}"
        wk = stats.get("weekly", {}).get(week, {})
        entries.append((uid_str, name, stats, wk))

    buf = _render_battle_lb_image(entries, week)
    file = discord.File(buf, filename="battle_lb.png")
    embed = discord.Embed(title="🦖 Battle Leaderboard 🦕", color=0xf1c40f)
    embed.set_image(url="attachment://battle_lb.png")
    embed.set_footer(text=f"Week: {week} • ⭐ = Weekly Champion titles")
    return embed, file

def _render_battle_lb_image(entries, week):
    """Render battle leaderboard as a table image."""
    BG_COLOR = (30, 33, 36)
    HEADER_COLOR = (180, 130, 20)
    ROW_EVEN = (44, 47, 51)
    ROW_ODD = (54, 57, 63)
    TEXT_COLOR = (255, 255, 255)
    DIM_TEXT = (185, 187, 190)
    GREEN = (67, 181, 129)
    RED = (240, 71, 71)
    ORANGE = (255, 165, 0)
    GOLD = (255, 215, 0)
    
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
        font_bold = font
        header_font = font
        title_font = font

    col_widths = [45, 170, 50, 45, 45, 55, 65, 55]
    col_headers = ["Rank", "Name", "W", "L", "T", "Streak", "Props", "Week"]
    row_height = 28
    padding = 10
    title_height = 40
    header_height = 26
    table_width = sum(col_widths) + padding * 2
    num_rows = max(1, len(entries))
    table_height = title_height + header_height + row_height * num_rows + padding

    img = Image.new("RGB", (table_width, table_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (4, title_height)], fill=HEADER_COLOR)
    draw.text((padding + 6, 8), "Battle Leaderboard", fill=TEXT_COLOR, font=title_font)

    y = title_height
    draw.rectangle([(0, y), (table_width, y + header_height)], fill=HEADER_COLOR)
    x = padding
    for i, header in enumerate(col_headers):
        draw.text((x + 3, y + 5), header, fill=TEXT_COLOR, font=header_font)
        x += col_widths[i]

    y += header_height
    medal_colors = [GOLD, (192,192,192), (205,127,50)]
    
    for idx, (uid_str, name, stats, wk) in enumerate(entries):
        row_color = ROW_EVEN if idx % 2 == 0 else ROW_ODD
        draw.rectangle([(0, y), (table_width, y + row_height)], fill=row_color)

        x = padding
        # Rank
        if idx < 3:
            rc = medal_colors[idx]
            draw.text((x + 3, y + 5), f"#{idx+1}", fill=rc, font=font_bold)
        else:
            draw.text((x + 3, y + 5), f"#{idx+1}", fill=DIM_TEXT, font=font)
        x += col_widths[0]

        # Name + stars
        stars = stats.get('champion_stars', 0)
        disp = name[:16] + ".." if len(name) > 16 else name
        if stars > 0:
            disp += f" ★{stars}"
        draw.text((x + 3, y + 5), disp, fill=TEXT_COLOR, font=font)
        x += col_widths[1]

        # W
        draw.text((x + 3, y + 5), str(stats['wins']), fill=GREEN, font=font_bold)
        x += col_widths[2]

        # L
        lc = RED if stats['losses'] > 0 else DIM_TEXT
        draw.text((x + 3, y + 5), str(stats['losses']), fill=lc, font=font)
        x += col_widths[3]

        # T
        draw.text((x + 3, y + 5), str(stats.get('ties', 0)), fill=DIM_TEXT, font=font)
        x += col_widths[4]

        # Streak
        s = stats.get('streak', 0)
        bs = stats.get('best_streak', 0)
        stxt = f"🔥{s}" if s >= 2 else str(s)
        sc = ORANGE if s >= 3 else (GREEN if s > 0 else DIM_TEXT)
        draw.text((x + 3, y + 5), stxt, fill=sc, font=font)
        x += col_widths[5]

        # Props
        pw, pl = stats.get('prop_wins', 0), stats.get('prop_losses', 0)
        draw.text((x + 3, y + 5), f"{pw}W/{pl}L", fill=DIM_TEXT, font=font)
        x += col_widths[6]

        # Week
        wk_w = wk.get("wins", 0)
        draw.text((x + 3, y + 5), str(wk_w), fill=GREEN if wk_w > 0 else DIM_TEXT, font=font)

        y += row_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def _build_dino_stats_embed(stats):
    """Build a dino leaderboard as an image table."""
    sorted_dinos = sorted(
        stats.items(),
        key=lambda x: (x[1].get('wins', 0), x[1].get('wins', 0) / max(1, x[1].get('total_battles', 1))),
        reverse=True
    )

    buf = _render_dino_stats_image(sorted_dinos[:15])
    file = discord.File(buf, filename="dino_stats.png")
    embed = discord.Embed(title="🦕 Dino Battle Leaderboard 🦖", color=0x2ecc71)
    embed.set_image(url="attachment://dino_stats.png")
    embed.set_footer(text="Species ranked by wins • 🔴 Carnivore 🟢 Herbivore")
    return embed, file

def _render_dino_stats_image(sorted_dinos):
    """Render dino species leaderboard as a table image."""
    BG_COLOR = (30, 33, 36)
    HEADER_COLOR = (46, 204, 113)
    ROW_EVEN = (44, 47, 51)
    ROW_ODD = (54, 57, 63)
    TEXT_COLOR = (255, 255, 255)
    DIM_TEXT = (185, 187, 190)
    GREEN = (67, 181, 129)
    RED = (240, 71, 71)
    GOLD = (255, 215, 0)

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        font_bold = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        header_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 13)
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
        font_bold = font
        header_font = font
        title_font = font

    col_widths = [45, 180, 50, 45, 45, 55, 55, 55]
    col_headers = ["Rank", "Dinosaur", "W", "L", "WR%", "Kills", "Deaths", "Flees"]
    row_height = 28
    padding = 10
    title_height = 40
    header_height = 26
    table_width = sum(col_widths) + padding * 2
    num_rows = max(1, len(sorted_dinos))
    table_height = title_height + header_height + row_height * num_rows + padding

    img = Image.new("RGB", (table_width, table_height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0), (4, title_height)], fill=HEADER_COLOR)
    draw.text((padding + 6, 8), "Dino Leaderboard", fill=TEXT_COLOR, font=title_font)

    y = title_height
    draw.rectangle([(0, y), (table_width, y + header_height)], fill=HEADER_COLOR)
    x = padding
    for i, header in enumerate(col_headers):
        draw.text((x + 3, y + 5), header, fill=TEXT_COLOR, font=header_font)
        x += col_widths[i]

    y += header_height
    medal_colors = [GOLD, (192,192,192), (205,127,50)]

    for idx, (did, ds) in enumerate(sorted_dinos):
        row_color = ROW_EVEN if idx % 2 == 0 else ROW_ODD
        draw.rectangle([(0, y), (table_width, y + row_height)], fill=row_color)

        x = padding
        # Rank
        if idx < 3:
            draw.text((x + 3, y + 5), f"#{idx+1}", fill=medal_colors[idx], font=font_bold)
        else:
            draw.text((x + 3, y + 5), f"#{idx+1}", fill=DIM_TEXT, font=font)
        x += col_widths[0]

        # Dinosaur name
        name = ds.get('name', did)
        dtype = ds.get('type', 'unknown')
        type_dot = "● " if dtype == 'carnivore' else "● "
        type_color = RED if dtype == 'carnivore' else GREEN
        # Draw type dot then name
        draw.text((x + 3, y + 5), "●", fill=type_color, font=font)
        disp_name = name[:17] + ".." if len(name) > 17 else name
        draw.text((x + 16, y + 5), disp_name, fill=TEXT_COLOR, font=font)
        x += col_widths[1]

        # W
        w = ds.get('wins', 0)
        draw.text((x + 3, y + 5), str(w), fill=GREEN, font=font_bold)
        x += col_widths[2]

        # L
        l = ds.get('losses', 0)
        draw.text((x + 3, y + 5), str(l), fill=RED if l > 0 else DIM_TEXT, font=font)
        x += col_widths[3]

        # WR%
        t = ds.get('total_battles', 0)
        wr = int((w / max(1, t)) * 100)
        wr_color = GREEN if wr >= 60 else (GOLD if wr >= 40 else RED)
        draw.text((x + 3, y + 5), f"{wr}%", fill=wr_color, font=font)
        x += col_widths[4]

        # Kills
        draw.text((x + 3, y + 5), str(ds.get('kills', 0)), fill=TEXT_COLOR, font=font)
        x += col_widths[5]

        # Deaths
        draw.text((x + 3, y + 5), str(ds.get('deaths', 0)), fill=DIM_TEXT, font=font)
        x += col_widths[6]

        # Flees
        draw.text((x + 3, y + 5), str(ds.get('flees', 0)), fill=DIM_TEXT, font=font)

        y += row_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


@bot.command(help="Start a dinosaur battle! Users have 60s to bet.")

async def dinobattle(ctx):
    import random
    import time
    
    # Battle channel restriction
    if battle_channel_id and ctx.channel.id != battle_channel_id:
        await ctx.send(f"⚔️ Battles are restricted to <#{battle_channel_id}>!", delete_after=8)
        return
    
    all_dinos = load_dinos()
    if len(all_dinos) < 2:
        await ctx.send("Not enough dinosaurs in the roster to battle. Need at least 2.")
        return

    dinos = random.sample(all_dinos, 2)
    dino_a = dict(dinos[0])
    dino_b = dict(dinos[1])

    for d in [dino_a, dino_b]:
        d.setdefault('cw', 3000)
        d.setdefault('hp', 500)
        d.setdefault('atk', 50)
        d.setdefault('armor', 1.0)
        d.setdefault('spd', 500)
        d['pack_size'] = 1

    # Pack Mechanics — engine handles individual members
    cw_ratio = float(dino_a['cw']) / float(dino_b['cw'])
    if cw_ratio > 3.0: # A is > 3x the size of B
        pack = random.randint(3, 8)
        dino_b['pack_size'] = pack
    elif cw_ratio < 0.33: # B is > 3x the size of A
        pack = random.randint(3, 8)
        dino_a['pack_size'] = pack

    await ctx.send("⚔️ **Generating fighters...**")
    
    buf = _render_vs_image(dino_a, dino_b)
    file = discord.File(buf, filename="dinobattle.png")
    
    end_time = int(time.time()) + 60
    embed = discord.Embed(
        title="🦖 DINOSAUR BATTLE! 🦕",
        description=f"Two titans enter the arena! Review their Path of Titans stats and place your bets!\n\n"
                    f"**{dino_a['name']}** vs **{dino_b['name']}**\n\n"
                    f"⏳ **Betting closes <t:{end_time}:R>!**",
        color=0xe67e22
    )
    embed.set_image(url="attachment://dinobattle.png")
    
    view = DinoBattleView(dino_a, dino_b)
    msg = await ctx.send(file=file, embed=embed, view=view)
    
    # ----------------------------------------------------
    # Stage 1: Wait for Bets (60 seconds)
    # ----------------------------------------------------
    await asyncio.sleep(60)
    
    for child in view.children:
        child.disabled = True
    
    # Update embed to show betting closed — keep VS card image visible
    embed.title = "⚔️ BETTING CLOSED — FIGHT STARTING!"
    embed.description = f"**{dino_a['name']}** vs **{dino_b['name']}**\n\nAll bets are locked in!"
    embed.color = 0xe74c3c
    try:
        await msg.edit(embed=embed, view=view)
    except:
        pass

    await asyncio.sleep(5)
    
    # ----------------------------------------------------
    # Stage 3: Combat Resolution via Battle Engine
    # ----------------------------------------------------
    import battle_engine
    
    result = battle_engine.simulate_battle(
        dino_a, dino_b,
        pack_a=dino_a.get('pack_size', 1),
        pack_b=dino_b.get('pack_size', 1)
    )
    
    fa = result["fighter_a"]
    fb = result["fighter_b"]
    
    # Show passive info if any
    passive_lines = []
    if fa.get("passive"):
        passive_lines.append(f"🔸 {fa['name']}: *{fa['passive']}*")
    if fb.get("passive"):
        passive_lines.append(f"🔸 {fb['name']}: *{fb['passive']}*")
    if passive_lines:
        passive_embed = discord.Embed(
            title="📋 Passive Abilities Active",
            description="\n".join(passive_lines),
            color=0x3498db
        )
        await ctx.send(embed=passive_embed)
    
    # Post turns as a single live-updating embed with HP bars
    # Green = left dino (A), Red = right dino (B)
    GREEN = "\u001b[0;32m"
    RED = "\u001b[0;31m"
    YELLOW = "\u001b[0;33m"
    RESET = "\u001b[0m"

    def _hp_bar_line(name, hp, max_hp, color, width=10):
        pct = max(0, hp / max_hp) if max_hp > 0 else 0
        filled = round(pct * width)
        empty = width - filled
        # Bar color changes based on HP %
        if pct > 0.5:
            bar_col = GREEN
        elif pct > 0.25:
            bar_col = YELLOW
        else:
            bar_col = RED
        return f"{color}{name}{RESET}  {bar_col}{'█' * filled}{'░' * empty}{RESET} {hp}/{max_hp}"

    a_max = fa['max_hp']
    b_max = fb['max_hp']
    a_hp_current = a_max
    b_hp_current = b_max
    a_name = fa['name']
    b_name = fb['name']
    combat_lines = []
    battle_embed = discord.Embed(title="⚔️ BATTLE IN PROGRESS", color=0xe67e22)
    battle_msg = await ctx.send(embed=battle_embed)

    for i, turn_lines in enumerate(result["turns"]):
        # Get HP snapshot for this turn
        if i < len(result.get("hp_snapshots", [])):
            snap = result["hp_snapshots"][i]
            a_hp_current = snap["a_hp"]
            b_hp_current = snap["b_hp"]
        elif i == len(result["turns"]) - 1:
            a_hp_current = fa['hp']
            b_hp_current = fb['hp']

        # Build HP bars header as one ANSI block (always visible at top)
        hp_header = (
            f"```ansi\n"
            f"{_hp_bar_line(a_name, max(0, a_hp_current), a_max, GREEN)}\n"
            f"{_hp_bar_line(b_name, max(0, b_hp_current), b_max, RED)}\n"
            f"```"
        )

        # Filter out summary lines
        turn_content = [line for line in turn_lines if not line.startswith("📊")]

        # Color combat log lines by which dino is acting
        def _color_line(line):
            if line.startswith(a_name) or a_name in line[:len(a_name)+5]:
                return f"```ansi\n{GREEN}{line}{RESET}\n```"
            elif line.startswith(b_name) or b_name in line[:len(b_name)+5]:
                return f"```ansi\n{RED}{line}{RESET}\n```"
            return line

        # Feed each line one by one within this turn
        visible_lines = []
        for line_idx, line in enumerate(turn_content):
            visible_lines.append(_color_line(line))
            desc = hp_header + "\n".join(visible_lines)
            if len(desc) > 3900:
                desc = desc[:3900]
            battle_embed.description = desc
            battle_embed.title = f"⚔️ Turn {i+1}"
            try:
                await battle_msg.edit(embed=battle_embed)
            except:
                pass
            await asyncio.sleep(1.5)

        # Brief pause after turn finishes before clearing
        await asyncio.sleep(1)

    # Determine winner & loser
    winner = None
    loser = None
    if result["winner"] == "a":
        winner = dino_a
        loser = dino_b
    elif result["winner"] == "b":
        winner = dino_b
        loser = dino_a

    if winner:
        winning_bets = view.bets_a if winner['name'] == dino_a['name'] else view.bets_b
        
        hp_left = fa['hp'] if result["winner"] == "a" else fb['hp']
        hp_max = fa['max_hp'] if result["winner"] == "a" else fb['max_hp']
        
        # Build two-column winner embed
        import battle_engine as _be
        winner_id = winner.get('id', '')
        w_family = _be.SPECIES_FAMILIES.get(winner_id.lower(), "generic").replace("_", " ").title()
        
        win_embed = discord.Embed(
            title=f"🏆 {result['winner_name']} WINS!",
            color=0x2ecc71
        )
        
        # Left column: Winner dino info
        dino_stats = load_dino_stats()
        w_stats = dino_stats.get(winner_id, {})
        w_wins = w_stats.get('wins', 0)
        w_losses = w_stats.get('losses', 0)
        w_kills = w_stats.get('kills', 0)
        w_total = w_stats.get('total_battles', 0)
        w_wr = int((w_wins / max(1, w_total)) * 100)
        
        win_embed.add_field(
            name=f"🦖 {result['winner_name']}",
            value=(
                f"**{w_family}** • {winner.get('type', '').capitalize()}\n"
                f"❤️ **{hp_left}/{hp_max}** HP remaining\n"
                f"📊 **{w_wins}W/{w_losses}L** ({w_wr}% WR)\n"
                f"💀 **{w_kills}** career kills\n"
                f"⚔️ **{w_total}** total battles"
            ),
            inline=True
        )
        
        # Right column: Top bettors (max 5)
        if winning_bets:
            # Show up to 5 winners
            bet_mentions = []
            for uid in list(winning_bets)[:5]:
                bet_mentions.append(f"<@{uid}>")
            bet_text = "\n".join(bet_mentions)
            if len(winning_bets) > 5:
                bet_text += f"\n*+{len(winning_bets) - 5} more*"
            win_embed.add_field(
                name="🎉 Winners",
                value=bet_text,
                inline=True
            )
        else:
            win_embed.add_field(name="📉 Bets", value="No winners!", inline=True)
        
        # Defeated dino info
        win_embed.add_field(
            name="",
            value=f"💀 **{result['loser_name']}** has been defeated!",
            inline=False
        )
        
        # Try to show winner's face
        winner_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", f"{winner_id}.png")
        if os.path.exists(winner_path):
            win_file = discord.File(winner_path, filename="winner.png")
            win_embed.set_thumbnail(url="attachment://winner.png")
            attachments = [win_file]
        else:
            attachments = []
    else:
        winning_bets = view.bets_tie
        win_embed = discord.Embed(
            title="⚖️ It's a TIE!",
            description="Both titans collapsed, or fought to a standstill!",
            color=0x95a5a6
        )
        attachments = []
        
    # --- Leaderboard Updates (Winner + Prop Bets) ---
    lb = load_dino_lb()
    all_bettors = view.bets_a.union(view.bets_b).union(view.bets_tie)
    for u in all_bettors:
        u_str = str(u)
        _ensure_lb_fields(lb.setdefault(u_str, {}))
            
    if winner:
        for u in all_bettors:
            u_str = str(u)
            _ensure_lb_fields(lb.setdefault(u_str, {}))
            if u in winning_bets:
                lb[u_str]["wins"] += 1
                lb[u_str]["streak"] = lb[u_str].get("streak", 0) + 1
                if lb[u_str]["streak"] > lb[u_str].get("best_streak", 0):
                    lb[u_str]["best_streak"] = lb[u_str]["streak"]
                _record_weekly_win(lb, u_str)
            else:
                lb[u_str]["losses"] += 1
                lb[u_str]["streak"] = 0
                _record_weekly_loss(lb, u_str)
    else:
        for u in all_bettors:
            u_str = str(u)
            _ensure_lb_fields(lb.setdefault(u_str, {}))
            if u in view.bets_tie:
                lb[u_str]["wins"] += 1
                lb[u_str]["streak"] = lb[u_str].get("streak", 0) + 1
                if lb[u_str]["streak"] > lb[u_str].get("best_streak", 0):
                    lb[u_str]["best_streak"] = lb[u_str]["streak"]
                _record_weekly_win(lb, u_str)
            else:
                lb[u_str]["ties"] += 1
                lb[u_str]["streak"] = 0
                _record_weekly_loss(lb, u_str)

    # --- Prop Bet Resolution ---
    prop_results = []
    # Flee
    flee_winners = view.prop_flee_yes if result.get("any_fled") else view.prop_flee_no
    flee_losers = view.prop_flee_no if result.get("any_fled") else view.prop_flee_yes
    if flee_winners or flee_losers:
        prop_results.append(f"🏃 **Flee**: {'Yes! Someone fled!' if result.get('any_fled') else 'No fleeing occurred'}")

    # Bleed Kill
    bleed_hit = result.get("bleed_kills", 0) > 0
    bleed_winners = view.prop_bleed_kill if bleed_hit else set()
    bleed_losers = view.prop_bleed_kill if not bleed_hit else set()
    if view.prop_bleed_kill:
        prop_results.append(f"🩸 **Bleed Kill**: {'Yes! {0} killed by bleed!'.format(result.get('bleed_kills', 0)) if bleed_hit else 'No bleed kills'}")

    # First Crit
    first_crit = result.get("first_crit_side")
    crit_winners = set()
    crit_losers = set()
    if first_crit == "a":
        crit_winners = view.prop_first_crit_a
        crit_losers = view.prop_first_crit_b
    elif first_crit == "b":
        crit_winners = view.prop_first_crit_b
        crit_losers = view.prop_first_crit_a
    if view.prop_first_crit_a or view.prop_first_crit_b:
        if first_crit:
            crit_name = dino_a['name'] if first_crit == 'a' else dino_b['name']
            prop_results.append(f"⚡ **First Crit**: {crit_name} landed it!")
        else:
            prop_results.append("⚡ **First Crit**: No critical hits occurred!")

    # KO Count
    total_kos = result.get("total_kos", 0)
    ko_over = total_kos >= 2
    ko_winners = view.prop_ko_over if ko_over else view.prop_ko_under
    ko_losers = view.prop_ko_under if ko_over else view.prop_ko_over
    if view.prop_ko_over or view.prop_ko_under:
        prop_results.append(f"💀 **KO Count**: {total_kos} total KOs ({'Over 2+!' if ko_over else 'Under!'})")

    # Tally prop wins/losses
    all_prop_winners = flee_winners | bleed_winners | crit_winners | ko_winners
    all_prop_losers = flee_losers | bleed_losers | crit_losers | ko_losers
    for u in all_prop_winners:
        u_str = str(u)
        _ensure_lb_fields(lb.setdefault(u_str, {}))
        lb[u_str]["prop_wins"] = lb[u_str].get("prop_wins", 0) + 1
        _record_weekly_prop(lb, u_str)
    for u in all_prop_losers:
        u_str = str(u)
        _ensure_lb_fields(lb.setdefault(u_str, {}))
        lb[u_str]["prop_losses"] = lb[u_str].get("prop_losses", 0) + 1
                
    save_dino_lb(lb)

    # Record per-dino battle stats
    _record_dino_battle(result, dino_a, dino_b)

    win_embed.set_footer(text="Combat powered by authentic Path of Titans formulas • Oath Bot")
    
    # Prop bet results
    if prop_results:
        prop_text = "\n".join(prop_results)
        if all_prop_winners:
            prop_mentions = " ".join([f"<@{uid}>" for uid in list(all_prop_winners)[:5]])
            prop_text += f"\n\n🎲 **Prop Winners**: {prop_mentions}"
            if len(all_prop_winners) > 5:
                prop_text += f" *+{len(all_prop_winners) - 5} more*"
        win_embed.add_field(name="🎲 Prop Bet Results", value=prop_text, inline=False)
        
    post_view = DinoPostBattleView()
    if attachments:
        await ctx.send(embed=win_embed, file=attachments[0], view=post_view)
    else:
        await ctx.send(embed=win_embed, view=post_view)

@bot.command(help="Look up a dinosaur's abilities, traits, and passives. Usage: !dino <name>")
async def dino(ctx, *, name: str = None):
    if not name:
        await ctx.send("Usage: `!dino <name>` — e.g. `!dino utahraptor`", delete_after=10)
        return

    all_dinos = load_dinos()
    if not all_dinos:
        await ctx.send("No dinosaurs in the roster yet!", delete_after=10)
        return

    # Fuzzy match — find closest dino
    query = name.lower().strip()
    match = None
    for d in all_dinos:
        if query == d['id'].lower() or query == d['name'].lower():
            match = d
            break
    if not match:
        for d in all_dinos:
            if query in d['id'].lower() or query in d['name'].lower():
                match = d
                break
    if not match:
        names = ", ".join(d['name'] for d in all_dinos[:20])
        await ctx.send(f"Dinosaur **{name}** not found. Available: {names}", delete_after=15)
        return

    import battle_engine as _be

    dino_id = match['id']
    family = _be.SPECIES_FAMILIES.get(dino_id.lower(), "generic")
    family_label = family.replace("_", " ").title()
    dtype = match.get('type', 'carnivore')
    cw = match.get('cw', 3000)
    group_slots = _be.get_group_slots(cw)
    passive = _be.PASSIVES.get(family)
    # Use custom abilities if defined, otherwise use family pool
    if match.get('custom_abilities'):
        abilities = match['custom_abilities']
    else:
        abilities = _be.get_ability_pool(family, dtype, 100)

    # Build embed
    color = 0xe74c3c if dtype == 'carnivore' else 0x2ecc71
    type_emoji = '🔴' if dtype == 'carnivore' else '🟢'
    embed = discord.Embed(
        title=f"🦕 {match['name']}",
        color=color
    )
    embed.add_field(name="Info", value=(
        f"{type_emoji} **{dtype.capitalize()}** • **{family_label}**\n"
        f"⚖️ CW: **{cw}** • 👥 Group Slots: **{group_slots}**"
    ), inline=False)

    if passive:
        embed.add_field(name=f"✨ Passive: {passive[0]}", value=passive[1], inline=False)

    # Abilities
    moves_text = ""
    for ab in abilities:
        if ab["base"] > 0:
            mult = ab["base"] / 100.0
            dmg = f"**{mult:.1f}x** ATK"
        else:
            dmg = "**Utility**"
        cd = f"CD: {ab['cd']}t" if ab["cd"] > 0 else "No CD"
        effects_parts = []
        for eff in ab.get("effects", []):
            t = eff.get("type", "")
            if t == "bleed":
                effects_parts.append(f"🩸 Bleed {eff.get('dur',0)}t")
            elif t == "bonebreak":
                effects_parts.append(f"🦴 Break {eff.get('dur',0)}t")
            elif t == "defense":
                effects_parts.append(f"🛡️ Def +{int(eff.get('reduction',0)*100)}%")
            elif t == "heal":
                effects_parts.append(f"💚 Heal {int(eff.get('pct',0)*100)}%")
        eff_str = " | ".join(effects_parts)
        if eff_str:
            eff_str = f" | {eff_str}"
        moves_text += f"⚔️ **{ab['name']}** — {dmg} ({cd}){eff_str}\n*{ab['desc']}*\n\n"

    embed.add_field(name="🎯 Ability Pool", value=moves_text.strip(), inline=False)

    if match.get('lore'):
        embed.add_field(name="📜 Lore", value=match['lore'][:200], inline=False)

    # Try to set thumbnail — custom upload first, then default
    avatar_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", f"{dino_id}.png")
    if not os.path.exists(avatar_path):
        avatar_path = os.path.join(os.path.dirname(__file__), "assets", "dinos", "defaults", f"{dino_id}.png")
    if os.path.exists(avatar_path):
        file = discord.File(avatar_path, filename="dino.png")
        embed.set_thumbnail(url="attachment://dino.png")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)

@bot.command(help="Show an annotated guide explaining each section of a battle card.")
async def battlehelp(ctx):
    help_path = os.path.join(os.path.dirname(__file__), "assets", "card_help_guide.png")
    embed = discord.Embed(
        title="📖 Battle Card Anatomy Guide",
        description="Each battle card displays your dinosaur's combat statistics. Here's what each section means:",
        color=0xf1c40f
    )
    embed.add_field(name="🖼️ Avatar Portrait", value="The dino's headshot image fills the upper card area, behind the frame overlay.", inline=False)
    embed.add_field(name="📛 Species Name", value="The dinosaur's display name on the banner. Subspecies/mod pack info appears below.", inline=False)
    embed.add_field(name="⚖️ CW — Combat Weight", value="Determines damage scaling. Heavier dinos deal more damage to lighter ones via the PoT formula: `Damage = Base × (AtkCW / DefCW)`.", inline=False)
    embed.add_field(name="❤️ HP — Health Points", value="Total hit points. When HP reaches 0, the dino is defeated. Below 25% HP, the dino may panic and flee.", inline=False)
    embed.add_field(name="⚔️ ATK — Attack Power", value="Base damage per hit. Modified by abilities, critical hits, hit zones (Head 1.2x, Body 1.0x, Tail 0.25x, Flank 0.8x).", inline=False)
    embed.add_field(name="🛡️ DEF — Defense Armor", value="Damage multiplier. 1.0 = normal, higher = tankier. Reduces all incoming damage.", inline=False)
    embed.add_field(name="⚡ SPD — Speed", value="Determines attack initiative. Faster dinos strike first each turn (85% chance).", inline=False)
    embed.set_footer(text="Use !dinobattle to start a fight • Oath Bot")
    if os.path.exists(help_path):
        file = discord.File(help_path, filename="card_help.png")
        embed.set_image(url="attachment://card_help.png")
        await ctx.send(embed=embed, file=file)
    else:
        await ctx.send(embed=embed)

@bot.command(help="Show the top bettors for Dino Battles.")
async def dinostats(ctx):
    lb = load_dino_lb()
    if not lb:
        await ctx.send("No Dino Battle bets on record yet!")
        return

    # Sort users by wins descending
    sorted_lb = sorted(lb.items(), key=lambda x: x[1]['wins'], reverse=True)
    
    desc = []
    for rank, (uid_str, stats) in enumerate(sorted_lb[:15]): # Top 15
        user = await bot.fetch_user(int(uid_str))
        name = user.display_name if user else f"User {uid_str}"
        w, l, t = stats['wins'], stats['losses'], stats['ties']
        s, bs = stats.get('streak', 0), stats.get('best_streak', 0)
        desc.append(f"**#{rank+1}** {name} — **{w}** W / **{l}** L / **{t}** Ties | Streak: 🔥{s} (Best: {bs})")

    embed = discord.Embed(
        title="🦖 Dino Battle Leaderboard 🦕",
        description="\n".join(desc),
        color=0xf1c40f
    )
    await ctx.send(embed=embed)

# ----------------------------
# Custom Help Command
# ----------------------------
def _build_everyone_embed():
    """Build the Everyone commands help embed."""
    embed = discord.Embed(
        title="📖  Attendance Bot — Help Menu",
        description="Use the buttons on the session sign-up message to **Attend**, **Standby**, **Not Attend**, or **Relieve** your spot.\n\nBelow are the available text commands:",
        color=0x2ecc71,
    )
    embed.add_field(name="✅  !schedule", value="Create a manual session sign-up in this channel.", inline=False)
    embed.add_field(name="📊  !stats", value="Show the attendance leaderboard (top 15 by rate).", inline=False)
    embed.add_field(name="🥚  !stats nesting", value="Show the Nesting leaderboard (Parents, Babies, Protectors).", inline=False)
    embed.add_field(name="📈  !mystats", value="View your personal attendance stats (only you can see it).", inline=False)
    embed.add_field(name="🔄  !swap @user", value="Request to swap your attending ↔ standby spot with another user.", inline=False)
    embed.add_field(name="📅  !days", value="Show the configured recurring session schedule.", inline=False)
    embed.add_field(name="⏪  !force", value="Force-post the next upcoming scheduled session.", inline=False)
    embed.add_field(name="🥚  !nest", value="Show current nesting status (parents, babies, protectors).", inline=False)
    embed.add_field(name="🦖  !dinobattle", value="Start a Pokémon-style random dinosaur card battle!", inline=False)
    embed.set_footer(text="Page 1/3 · Session buttons: Attend · Standby · Not Attending · Relieve Spot")
    return embed

def _build_admin_embed():
    """Build the Admin commands help embed."""
    embed = discord.Embed(
        title="🔒  Admin Commands",
        description="These commands can only be used by the bot admin.",
        color=0xe74c3c,
    )
    embed.add_field(name="👥  !setmax <n>", value="Set the maximum number of attendees (1–50).", inline=False)
    embed.add_field(name="📆  !addday <Day> <hour>", value="Add a recurring session day. Example: `!addday Thursday 20` (8 PM).", inline=False)
    embed.add_field(name="🗑️  !removeday <Day>", value="Remove all sessions on a given day. Example: `!removeday Thursday`.", inline=False)
    embed.add_field(name="👢  !kick @user", value="Remove a user from attending, standby, and not-attending lists.", inline=False)
    embed.add_field(name="🔄  !resetstats @user", value="Reset a user's attendance history to zero.", inline=False)
    embed.add_field(name="⏱️  !setgrace <minutes>", value="Set the check-in grace period (5–120 min). Default: 30.", inline=False)
    embed.add_field(name="⚠️  !setnoshow <n>", value="Set the no-show threshold for auto-standby. Default: 3.", inline=False)
    embed.add_field(name="⚙️  !settings", value="Show all current bot settings.", inline=False)
    embed.add_field(name="🦴  !settype <type>", value="Change session type: `hunt`, `nesting`, `growth`, `pvp`, `migration`.", inline=False)
    embed.add_field(name="🦕  !parent @user", value="Designate a nest parent (nesting mode only).", inline=False)
    embed.add_field(name="🐣  !baby @user", value="Designate a baby (nesting mode only).", inline=False)
    embed.set_footer(text="Page 2/3 · Admin only")
    return embed

def _build_test_embed():
    """Build the Test commands help embed."""
    embed = discord.Embed(
        title="🧪  Test & Fun Commands",
        description="These commands are used for testing features without triggering standard long-duration events.\n\n"
                    "**🌐 Web Dashboard & Setup**\n"
                    "The admin UI is hosted locally at `http://localhost:8080/` (or your VPS IP). Use it to manage the Battle Roster and active Testing Sessions!",
        color=0xf1c40f,  # Yellow
    )
    embed.add_field(name="🧪  !testsession [minutes]", value="Create a quick test session (default 1 min). Example: `!testsession 3`", inline=False)
    embed.add_field(name="🧪  !testsession <type> [minutes]", value="Create a fast test of a specific type (`hunt`, `nesting`, `growth`, `pvp`, `migration`). Example: `!testsession nesting 5`", inline=False)
    
    embed.add_field(name="🦖  !dinobattle", value="Starts a lethal 1v1 (or 1vPack) Path of Titans simulation! Users have **60 seconds** to place interactive bets (`Win`, `Loss`, or `Tie`) before the carnage reveals the victor.", inline=False)
    embed.add_field(name="🏆  !dinostats", value="Displays the Top 15 bettors by Win/Loss ratio and tracks current 🔥 Winning Streaks.", inline=False)
    
    embed.set_footer(text="Page 3/3 · Testing & Minigames")
    return embed

class HelpView(discord.ui.View):
    """Interactive help menu with page-navigation buttons."""
    def __init__(self, user_id, show_admin=False):
        super().__init__(timeout=120)  # buttons expire after 2 min
        self.user_id = user_id
        self.show_admin = show_admin
        # Only add the admin/test buttons if the user is admin
        if not show_admin:
            self.remove_item(self.show_admin_page)
            self.remove_item(self.show_test_page)

    @discord.ui.button(label="Everyone Commands", style=discord.ButtonStyle.success, emoji="📖")
    async def show_everyone_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help menu isn't for you. Type `!help` to get your own!", ephemeral=True)
            return
        footer = "Page 1/3 · Session buttons: Attend · Standby · Not Attending · Relieve Spot" if self.show_admin else "Session buttons: Attend · Standby · Not Attending · Relieve Spot"
        embed = _build_everyone_embed()
        embed.set_footer(text=footer)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Admin Commands", style=discord.ButtonStyle.danger, emoji="🔒")
    async def show_admin_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help menu isn't for you. Type `!help` to get your own!", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_build_admin_embed(), view=self)

    @discord.ui.button(label="Test Commands", style=discord.ButtonStyle.secondary, emoji="🧪")
    async def show_test_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help menu isn't for you. Type `!help` to get your own!", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_build_test_embed(), view=self)

@bot.command(help="Show this help menu.")
async def help(ctx):
    """Points users to the Menu button on the session embed."""
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send(
        f"{ctx.author.mention} Click the **📋 Menu** button on the session message to see help! Only you will see it.",
        delete_after=10
    )

# ----------------------------
# Automatic Scheduler
# ----------------------------
@tasks.loop(minutes=1)
async def auto_schedule_sessions():
    global last_posted_session
    now = datetime.now(EST)
    channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
    if not channel:
        return

    window_start = now - timedelta(seconds=30)
    window_end = now + timedelta(seconds=30)

    for sd in session_days:
        session_dt = next_run_time(sd["hour"], sd["weekday"])
        post_dt = session_dt - timedelta(hours=sd["post_hours_before"])
        if window_start <= post_dt <= window_end:
            h12 = sd["hour"] % 12 or 12
            ampm = "AM" if sd["hour"] < 12 else "PM"
            name = f"{sd['name']} {h12}{ampm} EST Session"
            session_key = f"{name}_{session_dt.isoformat()}"
            if last_posted_session == session_key:
                print(f"⏩ Skipping duplicate post for: {name}")
                return
            await create_schedule(channel, name, session_dt=session_dt)
            last_posted_session = session_key
            save_state()
            return

# ----------------------------
# Reminder DMs (1 hour before)
# ----------------------------
@tasks.loop(minutes=1)
async def session_reminders():
    global reminder_sent
    if not session_dt_str or reminder_sent:
        return

    try:
        session_dt = datetime.fromisoformat(session_dt_str)
        now = datetime.now(session_dt.tzinfo or EST)
        time_until = (session_dt - now).total_seconds()

        # 1 hour before (between 55-65 min before to catch window)
        if 55 * 60 <= time_until <= 65 * 60:
            reminder_sent = True
            save_state()
            count = 0
            for uid in attending_ids:
                try:
                    user = await bot.fetch_user(uid)
                    unix_ts = int(session_dt.timestamp())
                    await user.send(
                        f"⏰ **Reminder!** Session starts <t:{unix_ts}:R>!\n"
                        f"📋 **{session_name}**\n\nAre you still coming?",
                        view=ReminderView(uid)
                    )
                    count += 1
                except Exception as e:
                    print(f"❌ Couldn't remind user {uid}: {e}")
            print(f"📨 Sent reminders to {count} attendees")
    except Exception as e:
        print(f"❌ Reminder error: {e}")

# ----------------------------
# Check-In & No-Show Detection
# ----------------------------
@tasks.loop(minutes=1)
async def checkin_manager():
    global checkin_active, checkin_message_id
    if not session_dt_str:
        return

    try:
        session_dt = datetime.fromisoformat(session_dt_str)
        now = datetime.now(session_dt.tzinfo or EST)
        minutes_after = (now - session_dt).total_seconds() / 60

        # At session start: DM each attending user a check-in button
        if 0 <= minutes_after <= 2 and not checkin_active:
            checkin_active = True
            dm_sent = 0
            dm_failed = []
            print(f"📨 Sending check-in DMs to {len(attending_ids)} attending users...")
            for uid in list(attending_ids):
                try:
                    user = await bot.fetch_user(uid)
                    print(f"   📤 Sending DM to {user.display_name} ({uid})...")
                    dm_channel = await user.create_dm()
                    view = CheckInView(uid)
                    await dm_channel.send(
                        f"🟢 **Session is starting!** You have **{CHECKIN_GRACE_MINUTES} minutes** to check in.\n"
                        f"Click the button below to confirm your attendance:",
                        view=view
                    )
                    dm_sent += 1
                    print(f"   ✅ DM sent to {user.display_name} ({uid})")
                except discord.Forbidden as e:
                    dm_failed.append(uid)
                    print(f"   ❌ FORBIDDEN: Cannot DM user {uid}: {e}")
                except discord.HTTPException as e:
                    dm_failed.append(uid)
                    print(f"   ❌ HTTP ERROR: Cannot DM user {uid}: {e.status} {e.text}")
                except Exception as e:
                    dm_failed.append(uid)
                    print(f"   ❌ ERROR: Cannot DM user {uid}: {type(e).__name__}: {e}")

            # Post a brief notice in the channel
            channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
            if channel:
                notice = f"🟢 **Session is starting!** Check-in DMs sent to **{dm_sent}** attendee(s)."
                if dm_failed:
                    failed_mentions = ", ".join(f"<@{uid}>" for uid in dm_failed)
                    notice += f"\n⚠️ Could not DM: {failed_mentions} — they may have DMs disabled."
                await channel.send(notice)
            save_state()
            print(f"✅ Check-in DMs result: {dm_sent} success, {len(dm_failed)} failed")

        # After grace period: auto-relieve no-shows
        if CHECKIN_GRACE_MINUTES <= minutes_after <= CHECKIN_GRACE_MINUTES + 2 and checkin_active:
            checkin_active = False
            no_show_users = []
            checked_in_users = []

            for uid in list(attending_ids):  # copy list since we modify it
                if uid in checked_in_ids:
                    record_attendance(uid)
                    checked_in_users.append(uid)
                else:
                    record_no_show(uid)
                    no_show_users.append(uid)

                    # Warn when no-show rate is getting high (50%+ with 2+ signups)
                    stats = get_user_stats(uid)
                    total = stats["total_signups"]
                    if total >= 2:
                        rate = int((stats["no_shows"] / total) * 100)
                        if 50 <= rate < 60:
                            try:
                                user = await bot.fetch_user(uid)
                                await user.send(
                                    f"⚠️ **Warning:** Your no-show rate is **{rate}%**. "
                                    f"At 60%+ you'll be auto-placed on **standby**. Check in to improve!"
                                )
                            except:
                                pass

            # AUTO-RELIEVE: remove no-shows from attending, offer spots to standby
            for uid in no_show_users:
                to_remove = [u for u in attending if u.id == uid]
                for u in to_remove:
                    attending.remove(u)
                    if u not in not_attending:
                        not_attending.append(u)
                # DM the no-show
                try:
                    user = await bot.fetch_user(uid)
                    await user.send(
                        f"❌ You didn't check in within {CHECKIN_GRACE_MINUTES} minutes. "
                        f"Your spot has been **auto-relieved** and offered to standby."
                    )
                except:
                    pass

            sync_ids_from_users()

            # Offer freed spots to standby
            for _ in range(len(no_show_users)):
                await offer_next_standby()

            # Update embed
            if schedule_view:
                await schedule_view.update_embed()

            # Post results
            channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
            if channel:
                no_show_mentions = []
                for uid in no_show_users:
                    no_show_mentions.append(f"<@{uid}>")
                checked_mentions = []
                for uid in checked_in_users:
                    checked_mentions.append(f"<@{uid}>")

                msg_parts = []
                if no_show_users:
                    msg_parts.append(f"❌ **Auto-relieved ({len(no_show_users)}):** {', '.join(no_show_mentions)}")
                if checked_in_users:
                    msg_parts.append(f"✅ **Checked in ({len(checked_in_users)}):** {', '.join(checked_mentions)}")
                if msg_parts:
                    await channel.send("\n".join(msg_parts))

            print(f"📊 Check-in complete: {len(checked_in_users)} checked in, {len(no_show_users)} auto-relieved")
    except Exception as e:
        print(f"❌ Check-in manager error: {e}")

# ----------------------------
# Weekly Summary (Sunday 10PM EST)
# ----------------------------
weekly_summary_sent_week = None

@tasks.loop(minutes=1)
async def weekly_summary():
    global weekly_summary_sent_week
    now = datetime.now(EST)

    # Sunday = 6, 10 PM
    if now.weekday() != 6 or now.hour != 22:
        return

    week_key = now.isocalendar()[1]
    if weekly_summary_sent_week == week_key:
        return

    weekly_summary_sent_week = week_key

    if not attendance_history:
        return

    try:
        channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
        if not channel:
            return

        # Calculate weekly stats
        total_sessions = len([sd for sd in session_days])  # sessions per week
        total_attended = sum(d.get("attended", 0) for d in attendance_history.values())
        total_signups = sum(d.get("total_signups", 0) for d in attendance_history.values())

        # Find MVP (highest streak this week)
        mvp_id = None
        mvp_streak = 0
        for uid_str, data in attendance_history.items():
            if data.get("streak", 0) > mvp_streak:
                mvp_streak = data["streak"]
                mvp_id = uid_str

        mvp_mention = ""
        if mvp_id:
            try:
                mvp_user = await bot.fetch_user(int(mvp_id))
                mvp_mention = f"\n🏆 **MVP:** {mvp_user.mention} (🔥{mvp_streak} streak)"
            except:
                mvp_mention = f"\n🏆 **MVP:** User {mvp_id} (🔥{mvp_streak} streak)"

        embed = discord.Embed(
            title="📈 Weekly Summary",
            description=(
                f"**Sessions this week:** {total_sessions}\n"
                f"**Total sign-ups (all time):** {total_signups}\n"
                f"**Total attendances (all time):** {total_attended}"
                f"{mvp_mention}"
            ),
            color=0xf39c12
        )
        await channel.send(embed=embed)
        print("📈 Weekly summary posted")
    except Exception as e:
        print(f"❌ Weekly summary error: {e}")

# ----------------------------
# ----------------------------
# Settings updater (for dashboard)
# ----------------------------
def update_settings(data):
    global MAX_ATTENDING, CHECKIN_GRACE_MINUTES, NOSHOW_THRESHOLD
    global admin_role_names, beta_role_names, archive_channel_id, session_days
    global status_channel_id, status_start_msg, status_stop_msg
    global battle_channel_id
    global session_type, nest_parent_ids, nest_baby_ids, nest_protector_ids
    if "max_attending" in data:
        MAX_ATTENDING = int(data["max_attending"])
    if "checkin_grace" in data:
        CHECKIN_GRACE_MINUTES = int(data["checkin_grace"])
    if "noshow_threshold" in data:
        NOSHOW_THRESHOLD = int(data["noshow_threshold"])
    if "admin_role_names" in data:
        admin_role_names = [r.strip() for r in data["admin_role_names"] if r.strip()]
    if "beta_role_names" in data:
        beta_role_names = [r.strip() for r in data["beta_role_names"] if r.strip()]
    if "archive_channel_id" in data:
        archive_channel_id = int(data["archive_channel_id"])
    if "session_days" in data:
        session_days = data["session_days"]
    if "status_channel_id" in data:
        val = data["status_channel_id"]
        status_channel_id = int(val) if val else None
    if "battle_channel_id" in data:
        val = data["battle_channel_id"]
        battle_channel_id = int(val) if val else None
    if "status_start_msg" in data:
        status_start_msg = data["status_start_msg"]
    if "status_stop_msg" in data:
        status_stop_msg = data["status_stop_msg"]
    if "session_type" in data:
        session_type = data["session_type"] if data["session_type"] in SESSION_TYPES else 'hunt'
        if session_type != 'nesting':
            nest_parent_ids.clear()
            nest_baby_ids.clear()
            nest_protector_ids.clear()
    
    # Process nesting arrays when present
    if "nest_parent_ids" in data:
        nest_parent_ids.clear()
        nest_parent_ids.extend([str(uid) for uid in data["nest_parent_ids"]])
    if "nest_baby_ids" in data:
        nest_baby_ids.clear()
        nest_baby_ids.extend([str(uid) for uid in data["nest_baby_ids"]])
    if "nest_protector_ids" in data:
        nest_protector_ids.clear()
        nest_protector_ids.extend([str(uid) for uid in data["nest_protector_ids"]])
        
    save_state()
    # Force a refresh of the embed so it updates instantly
    try:
        loop = asyncio.get_running_loop()
        if session_dt_str and not session_ended:
            loop.create_task(refresh_open_embeds())
    except:
        pass

# ----------------------------
# Bot ready
# ----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    # Restore state from file
    load_state()
    load_history()

    # Sync user IDs to User objects
    await sync_users_from_ids()

    # Create and register persistent views
    global schedule_view
    schedule_view = ScheduleView()
    bot.add_view(schedule_view)
    bot.add_view(OfferView(None))  # register for DM button persistence
    bot.add_view(CheckInView())

    # Start task loops (guard against duplicate starts on reconnect)
    if not auto_schedule_sessions.is_running():
        auto_schedule_sessions.start()
    if not session_reminders.is_running():
        session_reminders.start()
    if not checkin_manager.is_running():
        checkin_manager.start()
    if not weekly_summary.is_running():
        weekly_summary.start()

    # ── Start Admin Dashboard ────────────────────────────────────
    dashboard.register_state_getters({
        "session_name":       lambda: session_name,
        "session_dt_str":     lambda: session_dt_str,
        "session_ended":      lambda: session_ended,
        "attending_ids":      lambda: attending_ids,
        "standby_ids":        lambda: standby_ids,
        "not_attending_ids":  lambda: not_attending_ids,
        "checked_in_ids":     lambda: checked_in_ids,
        "checkin_active":     lambda: checkin_active,
        "attendance_history": lambda: attendance_history,
        "max_attending":      lambda: MAX_ATTENDING,
        "noshow_threshold":   lambda: NOSHOW_THRESHOLD,
        "checkin_grace":      lambda: CHECKIN_GRACE_MINUTES,
        "session_days":       lambda: session_days,
        "admin_role_names":   lambda: admin_role_names,
        "beta_role_names":    lambda: beta_role_names,
        "archive_channel_id": lambda: archive_channel_id,
        "schedule_channel_id": lambda: SCHEDULE_CHANNEL_ID,
        "status_channel_id":   lambda: status_channel_id,
        "battle_channel_id":   lambda: battle_channel_id,
        "status_start_msg":    lambda: status_start_msg,
        "status_stop_msg":     lambda: status_stop_msg,
        "session_type":        lambda: session_type,
        "nest_parent_ids":     lambda: nest_parent_ids,
        "nest_baby_ids":       lambda: nest_baby_ids,
        "save_history":       save_history,
        "update_settings":    update_settings,
        "load_dinos":         load_dinos,
        "save_dinos":         save_dinos,
        "load_dino_lb":       load_dino_lb,
        "load_dino_stats":    load_dino_stats,
        "create_schedule":    create_schedule,
        "edit_current_session": edit_current_session,
    })
    await dashboard.start_dashboard(bot)

    print("✅ Bot is ready!")
    print(f"   Admin: {ADMIN_ID}")
    print(f"   Max attending: {MAX_ATTENDING}")
    print(f"   Session days: {len(session_days)}")
    print(f"   History: {len(attendance_history)} users tracked")

# ----------------------------
# Run Bot
# ----------------------------
async def main():
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN environment variable not set!")
        print("Set it with: export DISCORD_BOT_TOKEN='your_token_here'")
        return

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
