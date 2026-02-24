# Discord Attendance Bot

A Discord bot for managing session sign-ups with persistent attendance tracking.

## Features

- ✅ **Attending** - Sign up for the session (max 10)
- ❓ **Standby** - Join the waitlist (max 5)
- ❌ **Not Attending** - Mark yourself as not attending
- 🔄 **Relieve Spot** - Give up your spot to someone on standby
- 🔔 **Auto-promotion** - Standby users get DM when spot opens
- 💾 **Persistent State** - Survives bot restarts

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your bot token
```bash
export DISCORD_BOT_TOKEN='your_token_here'
```

### 3. Run the bot
```bash
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `!schedule` | Create a manual session |
| `!testsession` | Create a test session (1 min from now) |
| `!force` | Force create the next scheduled session |

## Configuration

Edit these values in `bot.py`:

```python
MAX_ATTENDING = 10      # Maximum attendees
MAX_STANDBY = 5         # Maximum standby
ALLOWED_GUILDS = [...]  # Allowed server IDs
SCHEDULE_CHANNEL_ID = ... # Channel for auto-scheduled sessions
```

## How It Works

1. **State Persistence**: All attendance data is saved to `state.json`
2. **No Timeouts**: Buttons work indefinitely (no 15-min limit)
3. **Restart Recovery**: Bot reconnects to existing message on restart
4. **Standby Promotion**: When spots open, standby users get DM offers

## Security

⚠️ **Never commit your bot token!** Use environment variables.
