# 🐷 AgPulse AI — Telegram Bot

---

## For Users

### How to get access

1. Find your Telegram user ID — message [@userinfobot](https://t.me/userinfobot), it will reply with your numeric ID (e.g. `123456789`)
2. Send your ID to the AgPulse admin to be added to the access list
3. Once added, find the bot in Telegram and send `/start`

### Getting started

1. Open Telegram
2. Find the bot: **@agpulse_proag_bot**
3. Send `/start`

You'll see a welcome message with example questions and a menu with buttons.

### Menu buttons

| Button | What you get |
|--------|-------------|
| 📊 **All Groups** | Crush Margin for all 18 pig groups — locked price vs current market |
| ⚠️ **Losing Groups** | Groups where the hedge is currently underwater |
| 🚛 **Best Packer** | Packer ranking by avg eval price |
| 🌽 **Feed Cost** | Corn price and feed cost per head for the last 7 days |
| 📈 **Market** | HE futures, national cash price, and basis |
| 🔔 **Barn Alerts** | Barns with abnormal drop in water usage |
| 📊 **Crush Margin Chart** | Bar chart: locked vs current price per group |
| 📈 **HE Futures Chart** | Line chart of lean hog futures over the last 60 days |
| 🌽 **Feed Cost Chart** | Chart of corn price and feed cost per head |
| ❓ **Help** | More example questions |

### Ask anything

You can also type a question in plain English:

```
How is PG-1007 doing?
Which groups are losing money?
What if hogs drop $10?
Where should I ship PG-1004?
Compare LRP vs put option
Any barn alerts?
Add 20% hedge to PG-1004
What if corn goes to $6?
Give me a weekly summary
```

### Reading the Crush Margin chart

- 🔵 **Blue bar** — price locked in through the hedge
- 🟡 **Gold bar** — current market price
- 🟢 **Green triangle (▲)** — hedge is favorable (locked above market)
- 🔴 **Red triangle (▼)** — hedge is unfavorable (market is above locked price)

---

## For Developers

### Setup

**1. Create and activate virtual environment**

```bash
python -m venv venv

# Mac/Linux:
source venv/bin/activate

# Windows:
.\venv\Scripts\Activate.ps1
```

If you get an execution policy error on Windows:
```bash
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**2. Create `bot.env` in the project root**

```env
TELEGRAM_BOT_TOKEN=your_token_here
ANTHROPIC_API_KEY=sk-ant-...
DB_PATH=swineintel.duckdb
ALLOWED_USERS=123456789,987654321
```

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ Yes | Token from @BotFather |
| `ANTHROPIC_API_KEY` | Optional | Enables AI narrative responses |
| `DB_PATH` | ✅ Yes | Path to swineintel.duckdb |
| `ALLOWED_USERS` | Optional | Comma-separated Telegram user IDs. Leave empty to allow everyone (dev mode) |

> ⚠️ Never commit `bot.env` to GitHub. Make sure it is in `.gitignore`.

**3. Install dependencies**

```bash
pip install -r requirements.txt
pip install -r requirements_bot.txt
```

**4. Build the database**

```bash
python build_database.py \
  --input Syracuse/ \
  --weather data/barn_env_with_weather.csv \
  --disease data/mshmp_disease_incidence.csv
```

**5. Run the bot**

```bash
python telegram_bot.py
```

Terminal should show:
```
🐷 AgPulse AI Telegram Bot is running...
```

### Adding a new client

1. Ask the client to message [@userinfobot](https://t.me/userinfobot) to get their Telegram user ID
2. Add their ID to `ALLOWED_USERS` in `bot.env`
3. Restart the bot (`Ctrl+C`, then `python telegram_bot.py`)

### Troubleshooting

| Error | Fix |
|-------|-----|
| `Please set TELEGRAM_BOT_TOKEN in bot.env` | `bot.env` is missing or in the wrong folder |
| `Database not found` | Run `build_database.py` first |
| `No module named 'telegram'` | `pip install -r requirements_bot.txt` |
| `No module named 'matplotlib'` | `pip install -r requirements_bot.txt` |
| Bot doesn't respond to `/start` | Check token in `bot.env` |
| "Access denied" in bot | Add Telegram user ID to `ALLOWED_USERS` in `bot.env` |
| AI returns raw data | Set `ANTHROPIC_API_KEY` in `bot.env` |
