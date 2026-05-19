"""
AgPulse AI — Telegram Bot
==========================
Telegram bot for ProAg-registered hog producers.
Connects to the same DuckDB backend as the Streamlit dashboard.

Requirements:
  pip install python-telegram-bot duckdb pandas anthropic python-dotenv matplotlib

Environment variables (bot.env):
  TELEGRAM_BOT_TOKEN=...      # token from @BotFather
  ANTHROPIC_API_KEY=...       # optional, enables AI narrative responses
  DB_PATH=swineintel.duckdb   # path to database
  ALLOWED_USERS=123456,789012 # comma-separated Telegram user_ids (ProAg client whitelist)
"""

import os
import io
import logging
import duckdb
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# Import query logic from SwineIntel ai_assistant.py
try:
    from ai_assistant import ask
    HAS_AI_ASSISTANT = True
except ImportError:
    HAS_AI_ASSISTANT = False

load_dotenv(dotenv_path="bot.env")
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_PATH = os.getenv("DB_PATH", "swineintel.duckdb")

_raw = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS: set[int] = {int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()}


def is_authorized(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


def get_db() -> duckdb.DuckDBPyConnection:
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"Database '{DB_PATH}' not found. "
            "Please run: python build_database.py"
        )
    return duckdb.connect(DB_PATH, read_only=True)


# ─────────────────────────────────────────────────────────────
# Chart generators — return BytesIO PNG
# ─────────────────────────────────────────────────────────────

STYLE = {
    "bg": "#0d1117",
    "ax_bg": "#161b22",
    "grid": "#21262d",
    "text": "#e6edf3",
    "green": "#3fb950",
    "red": "#f85149",
    "blue": "#58a6ff",
    "amber": "#d29922",
    "border": "#30363d",
}


def _apply_dark_style(fig, ax):
    fig.patch.set_facecolor(STYLE["bg"])
    ax.set_facecolor(STYLE["ax_bg"])
    ax.tick_params(colors=STYLE["text"], labelsize=9)
    ax.xaxis.label.set_color(STYLE["text"])
    ax.yaxis.label.set_color(STYLE["text"])
    ax.title.set_color(STYLE["text"])
    for spine in ax.spines.values():
        spine.set_edgecolor(STYLE["border"])
    ax.grid(True, color=STYLE["grid"], linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)


def chart_crush_margin(conn) -> io.BytesIO:
    """Bar chart: Locked vs Current Crush Margin per group."""
    df = conn.execute("""
        SELECT l.Pig_Group_ID,
               l.effective_floor_cwt  AS locked,
               h.current_market_cwt   AS current,
               h.pnl_per_head         AS pnl
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        ORDER BY l.Pig_Group_ID
    """).df().dropna(subset=["locked", "current"])

    if df.empty:
        return None

    groups = df["Pig_Group_ID"].tolist()
    x = range(len(groups))
    w = 0.35

    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_dark_style(fig, ax)

    bars_locked = ax.bar([i - w/2 for i in x], df["locked"], w,
                         label="Locked (hedge)", color=STYLE["blue"], alpha=0.85)
    bars_current = ax.bar([i + w/2 for i in x], df["current"], w,
                          label="Current market", color=STYLE["amber"], alpha=0.85)

    # Color pnl indicator on top
    for i, (_, row) in enumerate(df.iterrows()):
        pnl_val = row["pnl"] or 0
        color = STYLE["green"] if pnl_val >= 0 else STYLE["red"]
        marker = "^" if pnl_val >= 0 else "v"
        ax.plot(i, max(row["locked"], row["current"]) + 1.5,
                marker=marker, color=color, markersize=7, linestyle="None")

    ax.set_xticks(list(x))
    ax.set_xticklabels([g.replace("PG-", "PG\n") for g in groups], fontsize=8)
    ax.set_ylabel("$/cwt", color=STYLE["text"])
    ax.set_title("Crush Margin — Locked vs Current ($/cwt)", color=STYLE["text"], fontsize=12, pad=12)
    ax.legend(facecolor=STYLE["ax_bg"], edgecolor=STYLE["border"],
              labelcolor=STYLE["text"], fontsize=9)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=STYLE["bg"])
    buf.seek(0)
    plt.close(fig)
    return buf


def chart_he_futures(conn) -> io.BytesIO:
    """Line chart: HE front-month futures price over last 30 days."""
    df = conn.execute("""
        SELECT date, he_front_close, national_cash
        FROM hog_dashboard
        ORDER BY date DESC
        LIMIT 60
    """).df()

    if df.empty:
        return None

    df = df.sort_values("date")
    df["date"] = pd.to_datetime(df["date"])

    fig, ax = plt.subplots(figsize=(10, 4))
    _apply_dark_style(fig, ax)

    ax.plot(df["date"], df["he_front_close"], color=STYLE["blue"],
            linewidth=1.8, label="HE Futures")
    ax.plot(df["date"], df["national_cash"], color=STYLE["amber"],
            linewidth=1.4, linestyle="--", label="National Cash", alpha=0.85)

    ax.fill_between(df["date"], df["he_front_close"],
                    alpha=0.08, color=STYLE["blue"])

    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("$%.2f"))
    ax.set_ylabel("$/cwt", color=STYLE["text"])
    ax.set_title("Lean Hog Futures — HE Front Month", color=STYLE["text"], fontsize=12, pad=12)
    ax.legend(facecolor=STYLE["ax_bg"], edgecolor=STYLE["border"],
              labelcolor=STYLE["text"], fontsize=9)

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=STYLE["bg"])
    buf.seek(0)
    plt.close(fig)
    return buf


def chart_feed_cost(conn) -> io.BytesIO:
    """Dual-axis line chart: corn price + feed cost per head."""
    df = conn.execute("""
        SELECT date, corn_cents_bu, feed_cost_per_head
        FROM feed_cost
        ORDER BY date DESC
        LIMIT 60
    """).df()

    if df.empty:
        return None

    df = df.sort_values("date")
    df["date"] = pd.to_datetime(df["date"])

    fig, ax1 = plt.subplots(figsize=(10, 4))
    _apply_dark_style(fig, ax1)

    ax2 = ax1.twinx()
    ax2.set_facecolor(STYLE["ax_bg"])
    ax2.tick_params(colors=STYLE["text"], labelsize=9)
    for spine in ax2.spines.values():
        spine.set_edgecolor(STYLE["border"])

    ax1.plot(df["date"], df["corn_cents_bu"], color=STYLE["amber"],
             linewidth=1.8, label="Corn (¢/bu)")
    ax2.plot(df["date"], df["feed_cost_per_head"], color=STYLE["green"],
             linewidth=1.8, linestyle="--", label="Feed cost ($/hd)")

    ax1.set_ylabel("Corn ¢/bu", color=STYLE["amber"])
    ax2.set_ylabel("Feed $/head", color=STYLE["green"])
    ax2.yaxis.label.set_color(STYLE["green"])
    ax2.tick_params(colors=STYLE["text"])

    ax1.set_title("Feed Cost — Corn Price vs Cost per Head", color=STYLE["text"], fontsize=12, pad=12)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2,
               facecolor=STYLE["ax_bg"], edgecolor=STYLE["border"],
               labelcolor=STYLE["text"], fontsize=9)

    fig.autofmt_xdate(rotation=30, ha="right")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=STYLE["bg"])
    buf.seek(0)
    plt.close(fig)
    return buf


# ─────────────────────────────────────────────────────────────
# Message formatting helpers
# ─────────────────────────────────────────────────────────────

def fmt_group_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "Group not found."
    r = df.iloc[0]
    pnl = r.get("pnl_per_head", 0) or 0
    pnl_str = f"${pnl:+.2f}/head"
    return (
        f"📊 *{r['Pig_Group_ID']}*\n"
        f"Hedge: {r.get('Instrument_Type','—')} {r.get('Coverage_Percent',0):.0%}\n"
        f"Health: {r.get('Health_Status','—')}\n"
        f"Hedge P&L: {pnl_str}\n"
        f"Mortality: {r.get('nursery_mortality_pct',0):.1f}%\n"
        f"Market date: {str(r.get('est_market_date','—'))[:10]}"
    )


# ─────────────────────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────────────────────

def main_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 All Groups", callback_data="all_groups"),
         InlineKeyboardButton("⚠️ Losing Groups", callback_data="losing")],
        [InlineKeyboardButton("🚛 Best Packer", callback_data="best_packer"),
         InlineKeyboardButton("🌽 Feed Cost", callback_data="feed_cost")],
        [InlineKeyboardButton("📈 Market", callback_data="market"),
         InlineKeyboardButton("🔔 Barn Alerts", callback_data="barn_alerts")],
        [InlineKeyboardButton("📊 Crush Margin Chart", callback_data="chart_crush"),
         InlineKeyboardButton("📈 HE Futures Chart", callback_data="chart_he")],
        [InlineKeyboardButton("🌽 Feed Cost Chart", callback_data="chart_feed")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    return InlineKeyboardMarkup(buttons)


def group_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, 18, 3):
        row = []
        for j in range(i, min(i + 3, 18)):
            gid = f"PG-{1000 + j}"
            row.append(InlineKeyboardButton(gid, callback_data=f"group_{gid}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("◀ Back", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────────────────────────────────────────
# Handlers
# ─────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text(
            "⛔ Access denied. This bot is available to ProAg-registered clients only.\n"
            "Please contact your agricultural advisor to get access."
        )
        return

    await update.message.reply_text(
        f"🐷 *AgPulse AI* — Welcome, {user.first_name}!\n\n"
        "I help you track Crush Margin, hedging positions, and profitability "
        "for your pig groups in real time.\n\n"
        "*Example questions you can ask:*\n"
        "• `How is PG-1007 doing?`\n"
        "• `Which groups are losing money?`\n"
        "• `What if hogs drop $10?`\n"
        "• `Where should I ship PG-1004?`\n"
        "• `Give me a weekly summary`\n\n"
        "Or use the buttons below:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "🆘 *Example questions:*\n\n"
        "• `How is PG-1007 doing?`\n"
        "• `Which groups are losing money?`\n"
        "• `What if hogs drop $10?`\n"
        "• `Where should I ship PG-1004?`\n"
        "• `Compare LRP vs put option`\n"
        "• `Any barn alerts?`\n"
        "• `Add 20% hedge to PG-1004`\n"
        "• `What if corn goes to $6?`\n"
        "• `Give me a weekly summary`\n\n"
        "Use /start to return to the main menu."
    )
    msg = update.message or update.callback_query.message
    await msg.reply_text(text, parse_mode="Markdown")


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if not is_authorized(query.from_user.id):
        await query.edit_message_text("⛔ Access denied.")
        return

    data = query.data

    if data == "main_menu":
        try:
            await query.edit_message_text(
                "🐷 *AgPulse AI* — Main Menu",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
        except Exception:
            # Message is a photo or can't be edited — send a new one
            await query.message.reply_text(
                "🐷 *AgPulse AI* — Main Menu",
                parse_mode="Markdown",
                reply_markup=main_keyboard(),
            )
        return

    if data == "help":
        await help_command(update, context)
        return

    if data == "select_group":
        await query.edit_message_text(
            "Select a group:", reply_markup=group_keyboard()
        )
        return

    # ── Chart handlers ──────────────────────────────────────
    if data in ("chart_crush", "chart_he", "chart_feed"):
        try:
            conn = get_db()
        except FileNotFoundError as e:
            await query.edit_message_text(f"❌ {e}")
            return

        try:
            if data == "chart_crush":
                buf = chart_crush_margin(conn)
                caption = "📊 *Crush Margin — Locked vs Current*\n🟢 favorable  🔴 unfavorable"
            elif data == "chart_he":
                buf = chart_he_futures(conn)
                caption = "📈 *Lean Hog Futures — HE Front Month*"
            else:
                buf = chart_feed_cost(conn)
                caption = "🌽 *Feed Cost — Corn Price vs Cost per Head*"

            if buf is None:
                await query.edit_message_text("No data available for this chart.")
                return

            await query.message.reply_photo(
                photo=buf,
                caption=caption,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu")
                ]])
            )
            await query.delete_message()

        except Exception as e:
            logger.error(f"Chart error: {e}")
            await query.edit_message_text(
                f"❌ Could not generate chart: {e}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu")
                ]])
            )
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return

    # ── Data handlers ────────────────────────────────────────
    try:
        conn = get_db()
    except FileNotFoundError as e:
        await query.edit_message_text(f"❌ {e}")
        return

    try:
        if data == "all_groups":
            df = conn.execute("""
                SELECT l.Pig_Group_ID, l.head_to_finisher,
                       l.effective_floor_cwt, h.current_market_cwt,
                       h.pnl_per_head, l.est_market_date
                FROM lifecycle l
                LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
                ORDER BY l.est_market_date
            """).df()
            if df.empty:
                await query.edit_message_text("No data available.")
                return
            lines = ["📋 *All Groups — Crush Margin:*\n"]
            for _, r in df.iterrows():
                locked = r.get("effective_floor_cwt", 0) or 0
                current = r.get("current_market_cwt", 0) or 0
                pnl = r.get("pnl_per_head", 0) or 0
                emoji = "🟢" if pnl >= 0 else "🔴"
                lines.append(
                    f"{emoji} *{r['Pig_Group_ID']}*: "
                    f"${locked:.2f} locked | ${current:.2f} mkt | "
                    f"${pnl:+.2f}/hd"
                )
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📌 Group Detail", callback_data="select_group"),
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu"),
                ]])
            )

        elif data == "losing":
            df = conn.execute("""
                SELECT l.Pig_Group_ID, l.Instrument_Type,
                       h.hedge_price_cwt, h.current_market_cwt,
                       h.pnl_per_head, h.total_pnl
                FROM lifecycle l
                JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
                WHERE h.pnl_per_head < 0
                ORDER BY h.total_pnl ASC
            """).df()
            if df.empty:
                text = "✅ All hedge positions are currently profitable!"
            else:
                lines = [f"⚠️ *{len(df)} losing group(s):*\n"]
                for _, r in df.iterrows():
                    lines.append(
                        f"🔴 *{r['Pig_Group_ID']}*: "
                        f"${r['pnl_per_head']:.2f}/hd "
                        f"(total ${r['total_pnl']:,.0f})\n"
                        f"   {r['Instrument_Type']}: "
                        f"${r['hedge_price_cwt']:.2f} → mkt ${r['current_market_cwt']:.2f}"
                    )
                text = "\n".join(lines)
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu")
                ]])
            )

        elif data == "best_packer":
            df = conn.execute("SELECT * FROM packer_ranking ORDER BY rank LIMIT 5").df()
            if df.empty:
                await query.edit_message_text("Packer data not available.")
                return
            lines = ["🚛 *Packer Rankings:*\n"]
            for _, r in df.iterrows():
                medal = ["🥇", "🥈", "🥉", "4.", "5."][int(r.get("rank", 1)) - 1]
                lines.append(
                    f"{medal} *{r['Packer']} {r.get('Contract_Type','')}*\n"
                    f"   Avg eval: ${r['avg_eval_cwt']:.2f}/cwt "
                    f"(+${r['advantage_per_head']:.2f}/hd)"
                )
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu")
                ]])
            )

        elif data == "feed_cost":
            df = conn.execute("""
                SELECT date, corn_cents_bu, feed_cost_per_head
                FROM feed_cost ORDER BY date DESC LIMIT 7
            """).df()
            if df.empty:
                await query.edit_message_text("Feed cost data not available.")
                return
            latest = df.iloc[0]
            lines = [
                f"🌽 *Feed Cost* (last 7 days):\n",
                f"Today: {str(latest['date'])[:10]} | "
                f"Corn: {latest['corn_cents_bu']:.0f}¢/bu | "
                f"Feed: ${latest['feed_cost_per_head']:.2f}/hd\n",
            ]
            for _, r in df.iterrows():
                lines.append(
                    f"`{str(r['date'])[:10]}` "
                    f"{r['corn_cents_bu']:.0f}¢ corn → "
                    f"${r['feed_cost_per_head']:.2f}/hd"
                )
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🌽 Feed Cost Chart", callback_data="chart_feed"),
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu"),
                ]])
            )

        elif data == "market":
            df = conn.execute("""
                SELECT date, he_front_close, national_cash, basis
                FROM hog_dashboard ORDER BY date DESC LIMIT 5
            """).df()
            if df.empty:
                await query.edit_message_text("Market data not available.")
                return
            latest = df.iloc[0]
            lines = [
                f"📈 *Market Context*\n",
                f"Date: {str(latest['date'])[:10]}",
                f"HE Futures: ${latest['he_front_close']:.2f}/cwt",
                f"National Cash: ${latest['national_cash']:.2f}/cwt",
                f"Basis: ${latest['basis']:.2f}/cwt\n",
                "*Last 5 days:*",
            ]
            for _, r in df.iterrows():
                lines.append(
                    f"`{str(r['date'])[:10]}` "
                    f"HE ${r['he_front_close']:.2f} | "
                    f"Cash ${r['national_cash']:.2f}"
                )
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 HE Futures Chart", callback_data="chart_he"),
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu"),
                ]])
            )

        elif data == "barn_alerts":
            try:
                df = conn.execute("""
                    WITH daily_avg AS (
                        SELECT Site, Barn, Date, Water_Usage_Gallons,
                            AVG(Water_Usage_Gallons) OVER (
                                PARTITION BY Site, Barn
                                ORDER BY Date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                            ) as rolling_avg
                        FROM barn_env
                    )
                    SELECT Site, Barn, Date, Water_Usage_Gallons, rolling_avg,
                        ROUND((1 - Water_Usage_Gallons / NULLIF(rolling_avg, 0)) * 100, 1) as drop_pct
                    FROM daily_avg
                    WHERE Water_Usage_Gallons < rolling_avg * 0.85
                    ORDER BY Date DESC LIMIT 5
                """).df()
            except Exception:
                df = pd.DataFrame()

            if df.empty:
                text = "✅ No barn alerts. All metrics are within normal range."
            else:
                lines = [f"🔔 *{len(df)} water usage alert(s):*\n"]
                for _, r in df.iterrows():
                    lines.append(
                        f"🚨 *{r['Site']} / {r['Barn']}* ({str(r['Date'])[:10]})\n"
                        f"   Usage: {r['Water_Usage_Gallons']:.0f} gal "
                        f"vs avg {r['rolling_avg']:.0f} gal "
                        f"(↓{r['drop_pct']:.0f}%)"
                    )
                text = "\n".join(lines)
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀ Menu", callback_data="main_menu")
                ]])
            )

        elif data.startswith("group_"):
            group_id = data.replace("group_", "")
            df = conn.execute("""
                SELECT l.*, h.current_market_cwt, h.pnl_per_head, h.total_pnl
                FROM lifecycle l
                LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
                WHERE l.Pig_Group_ID = ?
            """, [group_id]).df()
            text = fmt_group_summary(df)
            await query.edit_message_text(
                text, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("◀ Select Group", callback_data="select_group")],
                    [InlineKeyboardButton("◀◀ Menu", callback_data="main_menu")],
                ])
            )

    except Exception as e:
        logger.error(f"Button handler error: {e}")
        await query.edit_message_text(
            f"❌ Error: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀ Menu", callback_data="main_menu")
            ]])
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle free-form text questions via AI Assistant."""
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Access denied.")
        return

    question = update.message.text.strip()
    if not question:
        return

    await update.message.chat.send_action("typing")

    try:
        conn = get_db()
    except FileNotFoundError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    try:
        if HAS_AI_ASSISTANT:
            response, intent, _ = ask(conn, question)
        else:
            response = _simple_query(conn, question)
        await update.message.reply_text(
            response, parse_mode="Markdown", reply_markup=main_keyboard()
        )
    except Exception as e:
        logger.error(f"Message handler error: {e}")
        await update.message.reply_text(
            f"Could not process your request: {e}\n\nTry using the menu buttons.",
            reply_markup=main_keyboard(),
        )
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _simple_query(conn, question: str) -> str:
    """Simple fallback without ai_assistant.py."""
    q = question.lower()
    for pg in [f"pg-{i}" for i in range(1000, 1020)]:
        if pg in q:
            gid = pg.upper()
            df = conn.execute("""
                SELECT l.*, h.current_market_cwt, h.pnl_per_head
                FROM lifecycle l
                LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
                WHERE l.Pig_Group_ID = ?
            """, [gid]).df()
            return fmt_group_summary(df)
    if any(w in q for w in ["packer", "ship", "route"]):
        df = conn.execute("SELECT * FROM packer_ranking ORDER BY rank LIMIT 3").df()
        if not df.empty:
            best = df.iloc[0]
            return f"🚛 Best packer: *{best['Packer']}* — ${best['avg_eval_cwt']:.2f}/cwt"
    return (
        "I understood your question but couldn't find the relevant data. "
        "Try the menu buttons or specify a group (e.g. `How is PG-1005 doing?`)"
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Telegram exception", exc_info=context.error)


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set TELEGRAM_BOT_TOKEN in bot.env!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_error_handler(error_handler)

    print("🐷 AgPulse AI Telegram Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
