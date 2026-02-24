import asyncio
import json
import os
from datetime import datetime, timedelta
import pytz
import discord
from discord.ext import commands, tasks

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

bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------------------
# Config
# ----------------------------
MAX_ATTENDING = 10
MAX_STANDBY = 5

ALLOWED_GUILDS = [1370907957830746194, 1475253514111291594]
SCHEDULE_CHANNEL_ID = 1370911001247223859
EST = pytz.timezone("US/Eastern")

# State file path
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")

# ----------------------------
# State Management - PERSISTENCE!
# ----------------------------
def load_state():
    """Load state from JSON file"""
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id, event_message_id, event_channel_id, session_name, session_dt_str
    
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
    return False

def save_state():
    """Save state to JSON file"""
    data = {
        'attending_ids': attending_ids,
        'standby_ids': standby_ids,
        'not_attending_ids': not_attending_ids,
        'pending_offer_id': pending_offer_id,
        'event_message_id': event_message_id,
        'event_channel_id': event_channel_id,
        'session_name': session_name,
        'session_dt': session_dt_str
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"❌ Error saving state: {e}")

# Initialize state
load_state()

# References to be populated at runtime
attending = []  # List of User objects
standby = []
not_attending = []
pending_offer = None
event_message = None
schedule_view = None

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

# ----------------------------
# Sync IDs to User objects
# ----------------------------
async def sync_users_from_ids():
    """Convert stored user IDs to User objects"""
    global attending, standby, not_attending, pending_offer, event_message
    
    attending = []
    standby = []
    not_attending = []
    pending_offer = None
    event_message = None
    
    # Get a guild to fetch users from
    guild = None
    for g in bot.guilds:
        if g.id in ALLOWED_GUILDS:
            guild = g
            break
    
    if not guild:
        print("❌ No allowed guild found")
        return
    
    # Sync attending
    for uid in attending_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                attending.append(member)
        except:
            pass
    
    # Sync standby
    for uid in standby_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                standby.append(member)
        except:
            pass
    
    # Sync not_attending
    for uid in not_attending_ids:
        try:
            member = await guild.fetch_member(uid)
            if member:
                not_attending.append(member)
        except:
            pass
    
    # Sync pending_offer
    if pending_offer_id:
        try:
            member = await guild.fetch_member(pending_offer_id)
            if member:
                pending_offer = member
        except:
            pass
    
    # Restore event message
    if event_message_id and event_channel_id:
        try:
            channel = await bot.fetch_channel(event_channel_id)
            if channel:
                event_message = await channel.fetch_message(event_message_id)
                print(f"✅ Restored event message: {event_message_id}")
        except Exception as e:
            print(f"❌ Could not restore event message: {e}")
    
    print(f"✅ Synced users: {len(attending)} attending, {len(standby)} standby, {len(not_attending)} not attending")

def sync_ids_from_users():
    """Convert User objects to IDs and save"""
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id
    
    attending_ids = [u.id for u in attending]
    standby_ids = [u.id for u in standby]
    not_attending_ids = [u.id for u in not_attending]
    pending_offer_id = pending_offer.id if pending_offer else None
    
    save_state()

# ----------------------------
# Build embed
# ----------------------------
def build_embed():
    embed = discord.Embed(title=session_name or "Session Sign-Up")
    attend_text = "\n".join(f"{i+1}. {user.mention} ✅" for i, user in enumerate(attending)) or "None"
    standby_text = "\n".join(f"{i+1}. {user.mention} ❓" for i, user in enumerate(standby)) or "None"
    not_attend_text = "\n".join(f"{user.mention} ❌" for user in not_attending) or "None"
    embed.add_field(name=f"Attending ({MAX_ATTENDING} Max)", value=attend_text, inline=False)
    embed.add_field(name=f"Standby ({MAX_STANDBY} Max)", value=standby_text, inline=False)
    embed.add_field(name="Not Attending", value=not_attend_text, inline=False)
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
# DM Offer View - NO TIMEOUT!
# ----------------------------
class OfferView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=None)  # ✅ NO TIMEOUT - buttons work forever!
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
# Main Schedule View - NO TIMEOUT!
# ----------------------------
class ScheduleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # ✅ NO TIMEOUT - buttons work forever!

    async def update_embed(self):
        if event_message:
            await event_message.edit(embed=build_embed(), view=self)

    @discord.ui.button(label="Attend", style=discord.ButtonStyle.success, emoji="✅", custom_id="schedule_attend")
    async def attend(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in attending:
            await interaction.response.send_message("You're already attending!", ephemeral=True)
            return
        if user in standby:
            await interaction.response.send_message("You're already on standby!", ephemeral=True)
            return
        if user in not_attending:
            not_attending.remove(user)
        if len(attending) < MAX_ATTENDING and pending_offer is None:
            attending.append(user)
        else:
            if len(standby) < MAX_STANDBY:
                standby.append(user)
            else:
                await interaction.response.send_message("Event and Standby are full.", ephemeral=True)
                return
        sync_ids_from_users()
        await interaction.response.send_message("Updated your attendance.", ephemeral=True)
        await self.update_embed()

    @discord.ui.button(label="Maybe / Standby", style=discord.ButtonStyle.secondary, emoji="❓", custom_id="schedule_standby")
    async def join_standby(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        if user in standby:
            await interaction.response.send_message("You're already on standby!", ephemeral=True)
            return
        if user in attending:
            attending.remove(user)
        if user in not_attending:
            not_attending.remove(user)
        if len(standby) < MAX_STANDBY:
            standby.append(user)
            sync_ids_from_users()
            await interaction.response.send_message("Added to standby.", ephemeral=True)
            await self.update_embed()
        else:
            await interaction.response.send_message("Standby list is full.", ephemeral=True)

    @discord.ui.button(label="Not Attending", style=discord.ButtonStyle.danger, emoji="❌", custom_id="schedule_not_attend")
    async def not_attend(self, interaction: discord.Interaction, button: discord.ui.Button):
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

    @discord.ui.button(label="Relieve Spot", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="schedule_relieve")
    async def relieve_spot(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
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

# ----------------------------
# Create / Reset Session
# ----------------------------
async def create_schedule(channel, session_name_arg: str, session_dt: datetime = None):
    global attending, standby, not_attending, pending_offer, event_message, schedule_view
    global session_name, session_dt_str, event_message_id, event_channel_id
    global attending_ids, standby_ids, not_attending_ids, pending_offer_id
    
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
    
    # Store session info
    session_name = session_name_arg
    session_dt_str = session_dt.isoformat() if session_dt else None

    if session_dt:
        unix_ts = int(session_dt.timestamp())
        session_time_str = f"<t:{unix_ts}:f>"
        title = f"Session Sign-Up: {session_name_arg} ({session_time_str})"
    else:
        title = f"Session Sign-Up: {session_name_arg}"

    embed = discord.Embed(title=title)
    embed.add_field(name=f"Attending ({MAX_ATTENDING} Max)", value="None", inline=False)
    embed.add_field(name=f"Standby ({MAX_STANDBY} Max)", value="None", inline=False)
    embed.add_field(name="Not Attending", value="None", inline=False)

    schedule_view = ScheduleView()
    event_message = await channel.send(content="@everyone", embed=embed, view=schedule_view)
    
    # Save message info for persistence
    event_message_id = event_message.id
    event_channel_id = channel.id
    save_state()

# ----------------------------
# Commands
# ----------------------------
@bot.command()
async def schedule(ctx):
    await create_schedule(ctx.channel, "Manual Session", session_dt=datetime.now(EST))

@bot.command()
async def testsession(ctx):
    test_dt = datetime.now(EST) + timedelta(minutes=1)
    await create_schedule(ctx.channel, "Test Session - Immediate", session_dt=test_dt)

# ----------------------------
# Force next session
# ----------------------------
@bot.command()
async def force(ctx):
    now = datetime.now(EST)
    session_days = [0, 1, 2]  # Monday, Tuesday, Wednesday
    next_session = None

    for day in session_days:
        session_dt = next_run_time(20, day)
        if session_dt >= now:
            next_session = session_dt
            session_name_arg = f"{session_dt.strftime('%A')} 8PM EST Session"
            break

    if not next_session:
        # fallback to first day of next week
        next_session = next_run_time(20, session_days[0])
        session_name_arg = f"{next_session.strftime('%A')} 8PM EST Session"

    try:
        channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
        await create_schedule(channel, session_name_arg, session_dt=next_session)
        await ctx.send(f"✅ Forced creation of session: {session_name_arg}")
    except Exception as e:
        await ctx.send(f"❌ Failed to post session: {e}")

# ----------------------------
# Automatic Scheduler
# ----------------------------
@tasks.loop(minutes=1)
async def auto_schedule_sessions():
    now = datetime.now(EST)
    channel = await bot.fetch_channel(SCHEDULE_CHANNEL_ID)
    if not channel:
        return

    window_start = now - timedelta(seconds=60)
    window_end = now + timedelta(seconds=60)

    monday_session_dt = next_run_time(20, 0)
    sunday_post_dt = monday_session_dt - timedelta(hours=12)
    if window_start <= sunday_post_dt <= window_end:
        await create_schedule(channel, "Monday 8PM EST Session", session_dt=monday_session_dt)
        return

    tuesday_session_dt = next_run_time(20, 1)
    monday_post_dt = tuesday_session_dt - timedelta(hours=20)
    if window_start <= monday_post_dt <= window_end:
        await create_schedule(channel, "Tuesday 8PM EST Session", session_dt=tuesday_session_dt)
        return

    wednesday_session_dt = next_run_time(20, 2)
    tuesday_post_dt = wednesday_session_dt - timedelta(hours=20)
    if window_start <= tuesday_post_dt <= window_end:
        await create_schedule(channel, "Wednesday 8PM EST Session", session_dt=wednesday_session_dt)
        return

# ----------------------------
# Bot ready
# ----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    
    # Restore state from file
    load_state()
    
    # Sync user IDs to User objects
    await sync_users_from_ids()
    
    # Register persistent view with custom_id matching
    bot.add_view(ScheduleView())
    
    # Create global schedule_view reference
    global schedule_view
    schedule_view = ScheduleView()
    
    # Start auto scheduler
    auto_schedule_sessions.start()
    
    print("✅ Bot is ready!")

# ----------------------------
# Run Bot
# ----------------------------
async def main():
    # Get token from environment variable
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN environment variable not set!")
        print("Set it with: export DISCORD_BOT_TOKEN='your_token_here'")
        return
    
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
