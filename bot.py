import asyncio
import io
import json
import os
from datetime import datetime, timedelta
import pytz
import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont

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
            "streak": 0, "best_streak": 0
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

    attending_ids = [u.id for u in attending]
    standby_ids = [u.id for u in standby]
    not_attending_ids = [u.id for u in not_attending]
    pending_offer_id = pending_offer.id if pending_offer else None

    save_state()

# ----------------------------
# Build embed (with countdown + streaks)
# ----------------------------
def build_embed():
    title = session_name or "Session Sign-Up"

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
        color = 0x2ecc71  # green - open

    embed = discord.Embed(title=title, color=color)

    # Attending list with streak badges
    if attending:
        attend_text = "\n".join(
            f"`{i+1}.` {user.mention}{streak_badge(user.id)}"
            for i, user in enumerate(attending)
        )
    else:
        attend_text = "*No one yet — be the first!*"

    # Standby list
    if standby:
        standby_text = "\n".join(
            f"`{i+1}.` {user.mention}{streak_badge(user.id)}"
            for i, user in enumerate(standby)
        )
    else:
        standby_text = "*Empty*"

    # Not attending
    if not_attending:
        not_attend_text = "\n".join(f"{user.mention}" for user in not_attending)
    else:
        not_attend_text = "*None*"

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
            embed.set_footer(text="Click a button below to sign up!")

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
    def __init__(self):
        super().__init__(timeout=None)

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
        await interaction.response.send_message(f"✅ {user.mention} checked in!", ephemeral=True)

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

    # Export to bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ----------------------------
# Main Schedule View
# ----------------------------
class ScheduleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_embed(self):
        if event_message:
            await event_message.edit(embed=build_embed(), view=self)

    @discord.ui.button(label="Attend", style=discord.ButtonStyle.success, emoji="✅", custom_id="schedule_attend")
    async def attend(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if session_has_started() or session_ended:
            msg = "🔴 Session has ended." if session_ended else "🔒 Session has already started — sign-ups are closed."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        if user in attending:
            await interaction.response.send_message("You're already attending!", ephemeral=True)
            return
        if user in standby:
            # Allow standby users to move to attending if there's room
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

        # No-show penalty: auto-standby if too many no-shows
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

    @discord.ui.button(label="Maybe / Standby", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="schedule_standby")
    async def join_standby(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if session_has_started() or session_ended:
            msg = "🔴 Session has ended." if session_ended else "🔒 Session has already started — sign-ups are closed."
            await interaction.response.send_message(msg, ephemeral=True)
            return
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

    @discord.ui.button(label="Not Attending", style=discord.ButtonStyle.danger, emoji="😞", custom_id="schedule_not_attend")
    async def not_attend(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if session_has_started() or session_ended:
            msg = "🔴 Session has ended." if session_ended else "🔒 Session has already started — sign-ups are closed."
            await interaction.response.send_message(msg, ephemeral=True)
            return
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

    @discord.ui.button(label="Relieve Spot", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="schedule_relieve")
    async def relieve_spot(self, interaction: discord.Interaction, button: discord.ui.Button):
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

    @discord.ui.button(label="End Session", style=discord.ButtonStyle.secondary, emoji="🔴", custom_id="schedule_end_session", row=1)
    async def end_session_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction.user):
            await interaction.response.send_message("❌ Only admins can end a session.", ephemeral=True)
            return
        if session_ended:
            await interaction.response.send_message("Session has already ended.", ephemeral=True)
            return
        await interaction.response.send_message("🔴 Ending session...", ephemeral=True)
        await end_session()

    @discord.ui.button(label="Menu", style=discord.ButtonStyle.secondary, emoji="📋", custom_id="schedule_menu", row=1)
    async def menu_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Whisper the help menu to the user (true ephemeral)."""
        user_is_admin = is_admin(interaction.user)
        embed = _build_everyone_embed()
        if user_is_admin:
            embed.set_footer(text="Page 1 · Only you can see this")
            # Build a combined view with admin page button
            admin_embed = _build_admin_embed()
            admin_embed.set_footer(text="Page 2 · Only you can see this")
            # Send both pages in one ephemeral response
            await interaction.response.send_message(
                embeds=[embed, admin_embed],
                ephemeral=True
            )
        else:
            embed.set_footer(text="Only you can see this")
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary, emoji="📊", custom_id="schedule_leaderboard", row=1)
    async def leaderboard_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
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

        # Build leaderboard entries
        entries = []
        guild = interaction.guild
        clicker_id = str(interaction.user.id)
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

        # Render the image
        img_bytes = _render_leaderboard_image(entries, clicker_id)
        file = discord.File(fp=img_bytes, filename="leaderboard.png")
        _leaderboard_cooldown = now
        await interaction.response.send_message(file=file)

# ----------------------------
# Create / Reset Session
# ----------------------------
async def create_schedule(channel, session_name_arg: str, session_dt: datetime = None):
    global attending, standby, not_attending, pending_offer, event_message, schedule_view
    global session_name, session_dt_str, event_message_id, event_channel_id
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id
    global reminder_sent, checkin_active, checked_in_ids, checkin_message_id
    global countdown_task, session_ended

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
                                    online_embed = discord.Embed(
                                        title="Session Online 🟢",
                                        description="Were live for OOTAH TIME!",
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
@bot.command(help="Create a Beta Led session. Usage: !schedule beta led <hour> (e.g. !schedule beta led 20 for 8PM)")
async def schedule(ctx, *args):
    """Create a Beta Led Session. Role-gated to Beta and Lead Beta roles."""
    if not has_beta_role(ctx.author):
        await ctx.send("❌ Only users with the **Beta** or **Lead Beta** role can schedule sessions.", delete_after=10)
        return

    # Parse: !schedule beta led 20  or  !schedule 20  or  !schedule
    hour = None
    for arg in args:
        try:
            hour = int(arg)
            break
        except ValueError:
            continue  # skip 'beta', 'led' etc.

    if hour is None:
        hour = 20  # default to 8 PM

    if hour < 0 or hour > 23:
        await ctx.send("❌ Hour must be 0–23 (24h format). Example: `!schedule beta led 20` for 8 PM.", delete_after=10)
        return

    # Build session datetime for today at the specified hour
    now = datetime.now(EST)
    session_dt = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    # If the time has already passed today, still create it (they might be late creating)
    h12 = hour % 12 or 12
    ampm = "PM" if hour >= 12 else "AM"
    await create_schedule(ctx.channel, "Beta Led Session", session_dt=session_dt)
    await ctx.send(f"✅ **Beta Led Session** scheduled for **{h12}{ampm} EST** today!", delete_after=10)

@bot.command(help="Create a quick test session. Usage: !testsession [minutes] (default 1, admin only)")
async def testsession(ctx, minutes: int = 1):
    if not await check_admin(ctx):
        return
    if minutes < 1 or minutes > 120:
        await ctx.send("❌ Minutes must be between 1 and 120.", delete_after=5)
        return
    test_dt = datetime.now(EST) + timedelta(minutes=minutes)
    await create_schedule(ctx.channel, f"Test Session ({minutes}min)", session_dt=test_dt)

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
# Stats / Leaderboard
# ----------------------------
@bot.command(help="Show the attendance leaderboard.")
async def stats(ctx):
    """Show the attendance leaderboard"""
    if not attendance_history:
        await ctx.send("📊 No attendance data yet.")
        return

    # Sort by attendance rate
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
    embed.add_field(name="📈  !mystats", value="View your personal attendance stats (only you can see it).", inline=False)
    embed.add_field(name="🔄  !swap @user", value="Request to swap your attending ↔ standby spot with another user.", inline=False)
    embed.add_field(name="📅  !days", value="Show the configured recurring session schedule.", inline=False)
    embed.add_field(name="⏩  !force", value="Force-post the next upcoming scheduled session.", inline=False)
    embed.set_footer(text="Page 1/2 · Session buttons: Attend · Standby · Not Attending · Relieve Spot")
    return embed

def _build_admin_embed():
    """Build the Admin commands help embed."""
    embed = discord.Embed(
        title="🔒  Admin Commands",
        description="These commands can only be used by the bot admin.",
        color=0xe74c3c,
    )
    embed.add_field(name="🧪  !testsession [minutes]", value="Create a quick test session (default 1 min). Example: `!testsession 5`", inline=False)
    embed.add_field(name="👥  !setmax <n>", value="Set the maximum number of attendees (1–50).", inline=False)
    embed.add_field(name="📆  !addday <Day> <hour>", value="Add a recurring session day. Example: `!addday Thursday 20` (8 PM).", inline=False)
    embed.add_field(name="🗑️  !removeday <Day>", value="Remove all sessions on a given day. Example: `!removeday Thursday`.", inline=False)
    embed.add_field(name="👢  !kick @user", value="Remove a user from attending, standby, and not-attending lists.", inline=False)
    embed.add_field(name="🔄  !resetstats @user", value="Reset a user's attendance history to zero.", inline=False)
    embed.add_field(name="⏱️  !setgrace <minutes>", value="Set the check-in grace period (5–120 min). Default: 30.", inline=False)
    embed.add_field(name="⚠️  !setnoshow <n>", value="Set the no-show threshold for auto-standby. Default: 3.", inline=False)
    embed.add_field(name="⚙️  !settings", value="Show all current bot settings.", inline=False)
    embed.set_footer(text="Page 2/2 · Admin only")
    return embed

class HelpView(discord.ui.View):
    """Interactive help menu with page-navigation buttons."""
    def __init__(self, user_id, show_admin=False):
        super().__init__(timeout=120)  # buttons expire after 2 min
        self.user_id = user_id
        self.show_admin = show_admin
        # Only add the admin button if the user is admin
        if not show_admin:
            self.remove_item(self.show_admin_page)

    @discord.ui.button(label="Everyone Commands", style=discord.ButtonStyle.success, emoji="📖")
    async def show_everyone_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help menu isn't for you. Type `!help` to get your own!", ephemeral=True)
            return
        footer = "Page 1/2 · Session buttons: Attend · Standby · Not Attending · Relieve Spot" if self.show_admin else "Session buttons: Attend · Standby · Not Attending · Relieve Spot"
        embed = _build_everyone_embed()
        embed.set_footer(text=footer)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Admin Commands", style=discord.ButtonStyle.danger, emoji="🔒")
    async def show_admin_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This help menu isn't for you. Type `!help` to get your own!", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_build_admin_embed(), view=self)

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

        # At session start: post check-in button
        if 0 <= minutes_after <= 2 and not checkin_active:
            checkin_active = True
            channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
            if channel:
                checkin_view = CheckInView()
                msg = await channel.send(
                    f"🟢 **Session is starting!** Attendees, please check in below.\n"
                    f"You have **{CHECKIN_GRACE_MINUTES} minutes** to check in or you'll be **auto-relieved**.",
                    view=checkin_view
                )
                checkin_message_id = msg.id
                save_state()
                print("✅ Check-in posted")

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
