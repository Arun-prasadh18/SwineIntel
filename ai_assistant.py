"""
SwineIntel — AI Assistant (v2)
===============================
AI is used in 4 specific ways:
  1. UNDERSTANDING: LLM classifies natural language questions into structured queries
  2. ANALYSIS: LLM connects dots across multiple data sources into recommendations
  3. COMMUNICATION: LLM generates consultant-quality narratives from raw numbers
  4. EXPLANATION: LLM explains anomalies with root causes and suggested actions

Falls back to rule-based + f-strings when no API key is set.
"""

import duckdb
import pandas as pd
import numpy as np
import json
import os

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


def has_ai():
    return HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY")


def call_claude(system, user_msg, max_tokens=600):
    """Single Claude API call with error handling."""
    try:
        client = Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}]
        )
        return msg.content[0].text
    except Exception as e:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# AI LAYER 1: UNDERSTANDING — Intent Classification
# ═══════════════════════════════════════════════════════════════════════════════

CLASSIFICATION_PROMPT = """You are an intent classifier for a hog production dashboard.
Classify the user's question into exactly ONE intent and extract parameters.

Valid intents:
- group_summary: asking about a specific pig group (extract group_id like PG-1007)
- packer_for_group: which packer to ship a specific group to (extract group_id)
- best_packer: general packer ranking question
- losing_groups: which groups are losing money / at risk / underwater
- unhedged_exposure: questions about unprotected / open / unhedged positions
- hedge_comparison: comparing LRP vs put options vs futures instruments
- barn_alerts: barn conditions, water usage, temperature, health monitoring
- disease_context: PRRS, PED, disease risk for a group (extract group_id if mentioned)
- feed_cost: feed cost, corn price, soybean meal questions
- market_context: futures prices, seasonal patterns, basis, market conditions
- scenario_price: "what if hogs drop/rise $X" (extract delta as number, negative for drops)
- scenario_feed: "what if corn goes to $X" (extract price in cents per bushel)
- scenario_add_hedge: "add X% hedge to group" (extract group_id and percentage)
- weekly_summary: general overview, how is everything, weekly update
- action_recommendation: "what should I do", "any recommendations", advice questions

Respond ONLY with JSON, no other text:
{"intent": "...", "group_id": null, "delta": null, "corn_cents": null, "pct": null}"""


def classify_intent_ai(question):
    """Use Claude to classify intent — handles ambiguous/complex questions."""
    result = call_claude(CLASSIFICATION_PROMPT, f'Question: "{question}"', max_tokens=150)
    if result:
        try:
            clean = result.strip().replace("```json", "").replace("```", "").strip()
            parsed = json.loads(clean)
            intent = parsed.get("intent", "weekly_summary")
            params = {}
            if parsed.get("group_id"):
                params["group_id"] = parsed["group_id"].upper()
            if parsed.get("delta") is not None:
                params["delta"] = float(parsed["delta"])
            if parsed.get("corn_cents") is not None:
                params["corn_cents"] = float(parsed["corn_cents"])
            if parsed.get("pct") is not None:
                params["pct"] = float(parsed["pct"])
            return intent, params
        except (json.JSONDecodeError, ValueError):
            pass
    # Fall back to rule-based
    return classify_intent_rules(question)


def classify_intent_rules(question):
    """Rule-based fallback when no API key is set."""
    q = question.lower().strip()

    for pg in [f"pg-{i}" for i in range(1000, 1020)]:
        if pg in q:
            if any(w in q for w in ['packer', 'ship', 'route', 'where']):
                return 'packer_for_group', {'group_id': pg.upper()}
            if any(w in q for w in ['disease', 'prrs', 'ped', 'health risk']):
                return 'disease_context', {'group_id': pg.upper()}
            if 'hedge' in q and any(w in q for w in ['add', 'more', 'increase']):
                pct = 20
                for token in q.split():
                    try:
                        v = float(token.replace('%', ''))
                        if 1 <= v <= 100: pct = v
                    except ValueError: pass
                return 'scenario_add_hedge', {'group_id': pg.upper(), 'pct': pct}
            return 'group_summary', {'group_id': pg.upper()}

    if any(w in q for w in ['what should', 'recommend', 'advice', 'suggest', 'what do i do']):
        return 'action_recommendation', {}
    if any(w in q for w in ['what if', 'scenario', 'what happens']):
        if 'corn' in q or 'feed' in q:
            price = 500
            for token in q.replace('$', '').split():
                try:
                    v = float(token)
                    if 3 <= v <= 8: price = v * 100
                    elif 300 <= v <= 800: price = v
                except ValueError: pass
            return 'scenario_feed', {'corn_cents': price}
        if any(w in q for w in ['drop', 'fall', 'decline', 'down']):
            delta = -5
            for token in q.replace('$', '').replace('-', '').split():
                try:
                    v = float(token)
                    if 1 <= v <= 30: delta = -v
                except ValueError: pass
            return 'scenario_price', {'delta': delta}
        if any(w in q for w in ['rise', 'rally', 'up', 'increase']):
            delta = 5
            for token in q.replace('$', '').split():
                try:
                    v = float(token)
                    if 1 <= v <= 30: delta = v
                except ValueError: pass
            return 'scenario_price', {'delta': delta}

    if any(w in q for w in ['packer', 'ship', 'which packer']): return 'best_packer', {}
    if any(w in q for w in ['lrp', 'put option', 'compare hedge', 'insurance']): return 'hedge_comparison', {}
    if any(w in q for w in ['barn', 'water', 'temperature', 'alert']): return 'barn_alerts', {}
    if any(w in q for w in ['feed cost', 'corn price', 'soybean']): return 'feed_cost', {}
    if any(w in q for w in ['market', 'futures', 'price', 'seasonal', 'basis']): return 'market_context', {}
    if any(w in q for w in ['losing', 'loss', 'worst', 'risk', 'underwater']): return 'losing_groups', {}
    if any(w in q for w in ['unhedged', 'exposure', 'unprotected']): return 'unhedged_exposure', {}
    if any(w in q for w in ['disease', 'prrs', 'ped']): return 'disease_context', {}
    if any(w in q for w in ['summary', 'overview', 'how are', 'update']): return 'weekly_summary', {}
    return 'weekly_summary', {}


def classify_intent(question):
    """Main classifier — uses AI if available, rules otherwise."""
    if has_ai():
        return classify_intent_ai(question)
    return classify_intent_rules(question)


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY FUNCTIONS — deterministic, no AI (data must be exact)
# ═══════════════════════════════════════════════════════════════════════════════

def get_group_summary(conn, group_id):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.Nursery_Site, l.finisher_site, l.finisher_barn,
               l.Received_Head, l.head_to_finisher, l.nursery_mortality_pct,
               l.days_in_nursery, l.est_market_date, l.Health_Status,
               l.Instrument_Type, l.Coverage_Percent, l.Head_Covered,
               l.effective_floor_cwt, l.unhedged_head, l.Contract_Month,
               l.nursery_avg_temp_f, l.finisher_avg_temp_f,
               h.current_market_cwt, h.pnl_per_cwt, h.pnl_per_head, h.total_pnl
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        WHERE l.Pig_Group_ID = ?
    """, [group_id]).df()

def get_all_groups(conn):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.finisher_site, l.head_to_finisher,
               l.Instrument_Type, l.Coverage_Percent, l.effective_floor_cwt,
               l.unhedged_pct, l.Health_Status, l.est_market_date,
               h.current_market_cwt, h.pnl_per_cwt, h.pnl_per_head, h.total_pnl
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        ORDER BY l.est_market_date
    """).df()

def get_losing_groups(conn):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.Instrument_Type, l.Coverage_Percent,
               h.hedge_price_cwt, h.current_market_cwt, h.pnl_per_head, h.total_pnl
        FROM lifecycle l JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        WHERE h.pnl_per_head < 0 ORDER BY h.total_pnl ASC
    """).df()

def get_best_packer(conn):
    return conn.execute("SELECT * FROM packer_ranking ORDER BY rank").df()

def get_packer_for_group(conn, group_id):
    group = conn.execute("SELECT * FROM lifecycle WHERE Pig_Group_ID = ?", [group_id]).df()
    ranking = conn.execute("SELECT * FROM packer_ranking ORDER BY rank LIMIT 3").df()
    return group, ranking

def get_hedge_comparison(conn):
    return conn.execute("""
        SELECT Commodity, coverageLevelPercent, coveragePrice,
               ROUND(perHeadPremium / 2.10, 2) as lrp_cost_cwt,
               "CME Premium" as cme_put_cost_cwt, endorsementLength
        FROM lrp_quotes WHERE Commodity = 'CME LEAN HOGS'
          AND coverageLevelPercent >= 0.90
        ORDER BY coverageLevelPercent DESC LIMIT 10
    """).df()

def get_barn_alerts(conn):
    try:
        return conn.execute("""
            WITH daily AS (
                SELECT Site, Barn, Date, Water_Usage_Gallons, Avg_Temperature_F,
                       AVG(Water_Usage_Gallons) OVER (
                           PARTITION BY Site, Barn ORDER BY Date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                       ) as avg14
                FROM barn_env)
            SELECT Site, Barn, Date, Water_Usage_Gallons, avg14, Avg_Temperature_F,
                   ROUND((1 - Water_Usage_Gallons/NULLIF(avg14,0))*100, 1) as drop_pct
            FROM daily WHERE Water_Usage_Gallons < avg14*0.85 AND avg14>0
            ORDER BY Date DESC LIMIT 5
        """).df()
    except: return pd.DataFrame()

def get_feed_cost(conn):
    return conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 30").df()

def get_market_context(conn):
    return conn.execute("SELECT * FROM hog_dashboard ORDER BY date DESC LIMIT 30").df()

def get_unhedged_exposure(conn):
    return conn.execute("""
        SELECT Pig_Group_ID, head_to_finisher, unhedged_head, unhedged_pct,
               Instrument_Type, Coverage_Percent, est_market_date
        FROM lifecycle WHERE unhedged_pct > 20 ORDER BY unhedged_head DESC
    """).df()

def get_disease_context(conn, group_id=None):
    if group_id:
        group = conn.execute("SELECT Pig_Group_ID, Placement_Date, Health_Status FROM lifecycle WHERE Pig_Group_ID = ?", [group_id]).df()
    else:
        group = pd.DataFrame()
    try:
        disease = conn.execute("SELECT * FROM disease WHERE season='2024-25' ORDER BY calendar_month").df()
    except: disease = pd.DataFrame()
    return group, disease

def get_weekly_data(conn):
    """Get all data needed for weekly summary / action recommendations."""
    groups = get_all_groups(conn)
    packer = conn.execute("SELECT * FROM packer_ranking LIMIT 3").df()
    feed = conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 1").df()
    try:
        alerts = get_barn_alerts(conn)
    except: alerts = pd.DataFrame()
    return groups, packer, feed, alerts


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO FUNCTIONS — deterministic math (no AI)
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_price_change(conn, delta_cwt):
    df = conn.execute("SELECT * FROM hedge_pnl").df()
    df['scenario_market'] = df['current_market_cwt'] + delta_cwt
    for i, row in df.iterrows():
        if row['instrument_type'] == 'Lean Hog Futures':
            pnl = row['hedge_price_cwt'] - df.loc[i, 'scenario_market']
        else:
            floor = row['hedge_price_cwt'] - row['premium_cwt']
            pnl = max(0, floor - df.loc[i, 'scenario_market']) - row['premium_cwt']
        df.loc[i, 'scenario_pnl_cwt'] = pnl
        df.loc[i, 'scenario_pnl_head'] = pnl * 2.10
        df.loc[i, 'scenario_total_pnl'] = pnl * 2.10 * row['head_covered']
    return df

def scenario_feed_change(conn, corn_cents_bu):
    feed = conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 1").df()
    current_corn = feed.corn_cents_bu.values[0]
    current_feed = feed.feed_cost_per_head.values[0]
    new_corn_ton = corn_cents_bu / 100 * (2000 / 56)
    new_sbm = feed.sbm_usd_ton.values[0]
    new_feed_ton = 0.75 * new_corn_ton + 0.20 * new_sbm
    new_feed_head = new_feed_ton * (800 / 2000)
    return {
        'current_corn_cents': current_corn, 'scenario_corn_cents': corn_cents_bu,
        'current_feed_per_head': round(current_feed, 2),
        'scenario_feed_per_head': round(new_feed_head, 2),
        'delta_per_head': round(new_feed_head - current_feed, 2),
    }

def scenario_add_hedge(conn, group_id, additional_pct):
    group = conn.execute("SELECT * FROM lifecycle WHERE Pig_Group_ID = ?", [group_id]).df()
    hedge = conn.execute("SELECT * FROM hedge_pnl WHERE Pig_Group_ID = ?", [group_id]).df()
    if len(group) == 0: return None
    g = group.iloc[0]
    current_pct = g['Coverage_Percent']
    total_head = g['head_to_finisher']
    new_pct = min(current_pct + additional_pct / 100, 1.0)
    new_covered = int(total_head * new_pct)
    return {
        'Pig_Group_ID': group_id,
        'current_coverage_pct': round(current_pct * 100, 1),
        'new_coverage_pct': round(new_pct * 100, 1),
        'current_head_covered': int(g['Head_Covered']),
        'new_head_covered': new_covered,
        'additional_head': new_covered - int(g['Head_Covered']),
        'additional_contracts': (new_covered - int(g['Head_Covered'])) // 400,
        'current_market_cwt': round(hedge.current_market_cwt.values[0], 2) if len(hedge) else 0,
        'unhedged_after': total_head - new_covered,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# AI LAYER 2: ANALYSIS — Action Recommendations
# ═══════════════════════════════════════════════════════════════════════════════

CONSULTANT_SYSTEM = """You are SwineIntel AI, an expert swine production and risk management 
consultant embedded in a hog operation dashboard. You have access to structured data about 
18 pig groups, their hedge positions, packer settlements, barn conditions, and market data.

Rules:
- Use ONLY the numbers provided in the data. Never invent or estimate numbers.
- Be direct and actionable — state what to do, not what "could" be done.
- Keep responses to 3-5 sentences.
- Use $/cwt for prices and $/head for per-animal economics.
- 1 cwt of carcass ≈ 2.10 per head (210 lb avg carcass).
- Coverage % = fraction of group hedged. Higher = more protected.
- Instruments: Futures (lock price, no upside), Put (floor + upside, costs premium), 
  LRP (floor + upside, 35% USDA subsidy makes it cheaper than puts).
- Negative hedge P&L when market rallied is NORMAL for futures — it means the producer 
  is making money on cash sales. Don't describe this as "losing money."
"""


def generate_action_recommendation(conn):
    """AI LAYER 2: Analyze all data and recommend the single most important action."""
    groups, packer, feed, alerts = get_weekly_data(conn)
    
    data_summary = f"""
GROUPS (sorted by coverage):
{groups[['Pig_Group_ID','Coverage_Percent','Instrument_Type','pnl_per_head','current_market_cwt']].sort_values('Coverage_Percent').to_string()}

LOWEST COVERAGE GROUPS (below 55%):
{groups[groups.Coverage_Percent < 0.55][['Pig_Group_ID','Coverage_Percent','current_market_cwt']].to_string()}

BEST PACKER: {packer.iloc[0]['Packer']} {packer.iloc[0]['Contract_Type']} at ${packer.iloc[0]['avg_eval_cwt']:.2f}/cwt (${packer.iloc[0]['advantage_per_head']:.2f}/head advantage)

LATEST FEED COST: ${feed.iloc[0]['feed_cost_per_head']:.2f}/head (corn at {feed.iloc[0]['corn_cents_bu']:.0f} cents/bu)

BARN ALERTS: {len(alerts)} water anomalies detected
"""
    
    if has_ai():
        response = call_claude(
            CONSULTANT_SYSTEM,
            f"Based on this data, what are the 2-3 most important actions the producer should take THIS WEEK? Be specific — name groups, dollar amounts, and instruments.\n\n{data_summary}"
        )
        if response:
            return response
    
    # Fallback
    low_cov = groups[groups.Coverage_Percent < 0.55]
    if len(low_cov):
        worst = low_cov.sort_values('Coverage_Percent').iloc[0]
        return (f"**Priority action:** Add coverage on **{worst.Pig_Group_ID}** — only {worst.Coverage_Percent:.0%} hedged "
                f"with market at ${worst.current_market_cwt:.2f}/cwt. "
                f"Consider LRP at 95% coverage (saves ~$1.20/cwt vs CME puts). "
                f"Ship next marketable group to {packer.iloc[0]['Packer']} {packer.iloc[0]['Contract_Type']} "
                f"for ${packer.iloc[0]['advantage_per_head']:.2f}/head advantage.")
    return "All groups adequately hedged. Monitor barn conditions and market prices."


# ═══════════════════════════════════════════════════════════════════════════════
# AI LAYER 3: COMMUNICATION — Response Generation
# ═══════════════════════════════════════════════════════════════════════════════

def generate_response_ai(question, data_text, intent):
    """Use Claude to generate consultant-quality response from query results."""
    response = call_claude(
        CONSULTANT_SYSTEM,
        f"Question: {question}\nIntent: {intent}\n\nData:\n{data_text}\n\nProvide a 3-5 sentence actionable response using ONLY numbers from the data."
    )
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# AI LAYER 4: EXPLANATION — Barn Alert Root Cause Analysis
# ═══════════════════════════════════════════════════════════════════════════════

def explain_barn_alert(conn, alert_data):
    """AI explains why a barn alert fired and what to do about it."""
    if not has_ai() or len(alert_data) == 0:
        return None
    
    alert_text = alert_data.to_string()
    
    # Get which groups are in the affected barns
    try:
        affected = conn.execute("""
            SELECT Pig_Group_ID, finisher_site, finisher_barn, head_to_finisher, Health_Status
            FROM lifecycle
        """).df()
    except:
        affected = pd.DataFrame()
    
    response = call_claude(
        CONSULTANT_SYSTEM,
        f"""A barn monitoring alert has been triggered:

{alert_text}

Groups currently housed in these barns:
{affected.to_string()}

Explain: (1) the most likely causes of the water usage drop, (2) which groups are at risk, 
(3) specific actions to take today. Be practical — this is for a farmer, not a vet textbook."""
    )
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# AI-GENERATED WEEKLY INSIGHT
# ═══════════════════════════════════════════════════════════════════════════════

def generate_weekly_insight(conn):
    """AI analyzes all data and writes a weekly briefing."""
    groups, packer, feed, alerts = get_weekly_data(conn)
    
    data_context = f"""
ALL GROUPS:
{groups[['Pig_Group_ID','Coverage_Percent','Instrument_Type','pnl_per_head','current_market_cwt','Health_Status']].to_string()}

PACKER RANKING (top 3):
{packer.to_string()}

FEED COST: ${feed.iloc[0]['feed_cost_per_head']:.2f}/head

BARN ALERTS: {len(alerts)} anomalies
{alerts.to_string() if len(alerts) else 'None'}
"""
    
    if has_ai():
        response = call_claude(
            CONSULTANT_SYSTEM,
            f"""Write a weekly briefing for this hog operation in 4-5 sentences. Cover:
1. The strongest and weakest positions (name specific groups and dollar amounts)
2. Groups that need immediate attention (low coverage, health concerns)  
3. One specific action recommendation for this week
4. Any barn alerts that need follow-up

{data_context}""",
            max_tokens=400
        )
        if response:
            return f"**Weekly Insight (AI-generated)**\n\n{response}"
    
    # Fallback: template-based
    best = groups.sort_values('pnl_per_head', ascending=False).iloc[0]
    worst = groups.sort_values('pnl_per_head').iloc[0]
    low_cov = groups[groups.Coverage_Percent < 0.55]
    
    msg = "**Weekly Insight**\n\n"
    msg += f"Strongest: **{best.Pig_Group_ID}** at ${best.pnl_per_head:+.2f}/head. "
    msg += f"Weakest: **{worst.Pig_Group_ID}** at ${worst.pnl_per_head:+.2f}/head.\n\n"
    if len(low_cov):
        msg += f"⚠️ {len(low_cov)} groups below 55% coverage: {', '.join(low_cov.Pig_Group_ID.tolist())}.\n\n"
    if len(packer):
        msg += f"🏭 Best packer: {packer.iloc[0]['Packer']} {packer.iloc[0]['Contract_Type']} (${packer.iloc[0]['advantage_per_head']:.2f}/head advantage)."
    return msg


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK FORMATTERS (no AI, still useful)
# ═══════════════════════════════════════════════════════════════════════════════

def format_fallback(intent, result, question):
    if intent == 'group_summary' and isinstance(result, pd.DataFrame) and len(result):
        r = result.iloc[0]
        pnl = f"${r.get('pnl_per_head',0):+.2f}/head" if pd.notna(r.get('pnl_per_head')) else "N/A"
        return (f"**{r['Pig_Group_ID']}** — {r.get('Instrument_Type','N/A')} at "
                f"{r.get('Coverage_Percent',0):.0%} coverage. Health: {r.get('Health_Status','N/A')}. "
                f"Hedge P&L: {pnl} ({r.get('head_to_finisher',0):,.0f} head). "
                f"Nursery mortality: {r.get('nursery_mortality_pct',0):.1f}%. "
                f"Est. market: {str(r.get('est_market_date','N/A'))[:10]}.")

    if intent == 'best_packer' and isinstance(result, pd.DataFrame) and len(result):
        b = result.iloc[0]
        return (f"Best combo: **{b['Packer']} {b['Contract_Type']}** at ${b['avg_eval_cwt']:.2f}/cwt "
                f"(${b['advantage_per_head']:.2f}/head advantage, {int(b['loads'])} loads).")

    if intent == 'packer_for_group' and isinstance(result, tuple) and len(result) == 2:
        group, ranking = result
        if len(group) and len(ranking):
            g = group.iloc[0]
            b = ranking.iloc[0]
            head = g.get('head_to_finisher', g.get('Received_Head', 0))
            savings = b['advantage_per_head'] * head
            return (f"**{g.get('Pig_Group_ID', 'Group')}** ({int(head):,} head): "
                    f"Ship to **{b['Packer']} {b['Contract_Type']}** — "
                    f"${b['avg_eval_cwt']:.2f}/cwt, ${b['advantage_per_head']:.2f}/head advantage. "
                    f"Est. total advantage: **${savings:,.0f}**.")

    if intent == 'losing_groups' and isinstance(result, pd.DataFrame):
        if len(result) == 0:
            return "All hedge positions currently profitable."
        w = result.iloc[0]
        return (f"{len(result)} groups with negative hedge P&L. Worst: **{w['Pig_Group_ID']}** "
                f"at ${w['pnl_per_head']:.2f}/head (${w['total_pnl']:,.0f}). "
                f"Note: negative hedge P&L when market rallied means the producer is making money on cash sales — "
                f"the hedge locked in a lower price, but unhedged head benefits from the higher market.")

    if intent == 'scenario_price' and isinstance(result, pd.DataFrame):
        total = result.scenario_total_pnl.sum()
        current = result.total_pnl.sum()
        return (f"Hedge P&L: ${current:+,.0f} → ${total:+,.0f} (${total-current:+,.0f}). "
                f"{(result.scenario_pnl_head > 0).sum()}/{len(result)} groups with positive hedge P&L.")

    if intent == 'scenario_feed' and isinstance(result, dict):
        return (f"Feed: ${result['current_feed_per_head']:.2f} → ${result['scenario_feed_per_head']:.2f}/head "
                f"(${result['delta_per_head']:+.2f}).")

    if intent == 'scenario_add_hedge' and isinstance(result, dict) and result:
        return (f"**{result['Pig_Group_ID']}**: {result['current_coverage_pct']:.0f}% → {result['new_coverage_pct']:.0f}% "
                f"(+{result['additional_head']} head = {result['additional_contracts']} contracts). "
                f"Unhedged after: {result['unhedged_after']} head.")

    if intent == 'unhedged_exposure' and isinstance(result, pd.DataFrame):
        total = result.unhedged_head.sum()
        return (f"{len(result)} groups with >20% unhedged. Total: {total:,.0f} exposed head. "
                f"Most exposed: **{result.iloc[0]['Pig_Group_ID']}** with {result.iloc[0]['unhedged_head']:,.0f} unhedged.")

    if intent == 'barn_alerts' and isinstance(result, pd.DataFrame):
        if len(result) == 0:
            return "No barn anomalies detected."
        a = result.iloc[0]
        return (f"{len(result)} water anomalies. Most recent: **Barn {a.get('Barn','')}** at {a.get('Site','')}, "
                f"water down {a.get('drop_pct',0):.0f}% on {str(a.get('Date',''))[:10]}.")

    if intent == 'hedge_comparison' and isinstance(result, pd.DataFrame) and len(result):
        return (f"LRP vs CME put comparison ({len(result)} quotes shown). "
                f"Coverage prices range ${result.iloc[-1]['coveragePrice']:.2f} → ${result.iloc[0]['coveragePrice']:.2f}/cwt.")

    if isinstance(result, tuple):
        parts = [df.to_string() if isinstance(df, pd.DataFrame) else str(df) for df in result]
        return "\n\n".join(parts)
    if isinstance(result, pd.DataFrame):
        return result.head(5).to_string()
    return str(result)


# ═══════════════════════════════════════════════════════════════════════════════
# EXECUTE QUERY
# ═══════════════════════════════════════════════════════════════════════════════

def execute_query(conn, intent, params):
    handlers = {
        'group_summary':       lambda: get_group_summary(conn, params.get('group_id', '')),
        'losing_groups':       lambda: get_losing_groups(conn),
        'best_packer':         lambda: get_best_packer(conn),
        'packer_for_group':    lambda: get_packer_for_group(conn, params.get('group_id', '')),
        'hedge_comparison':    lambda: get_hedge_comparison(conn),
        'barn_alerts':         lambda: get_barn_alerts(conn),
        'feed_cost':           lambda: get_feed_cost(conn),
        'market_context':      lambda: get_market_context(conn),
        'unhedged_exposure':   lambda: get_unhedged_exposure(conn),
        'weekly_summary':      lambda: get_weekly_data(conn),
        'disease_context':     lambda: get_disease_context(conn, params.get('group_id')),
        'action_recommendation': lambda: 'action',  # handled separately
        'scenario_price':      lambda: scenario_price_change(conn, params.get('delta', -5)),
        'scenario_feed':       lambda: scenario_feed_change(conn, params.get('corn_cents', 500)),
        'scenario_add_hedge':  lambda: scenario_add_hedge(conn, params.get('group_id', ''), params.get('pct', 20)),
    }
    return handlers.get(intent, handlers['weekly_summary'])()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def ask(conn, question):
    """Main entry: question → classify → query → respond."""
    # Step 1: AI classifies intent (or rule-based fallback)
    intent, params = classify_intent(question)
    
    # Step 2: Action recommendation is a special case (AI-only analysis)
    if intent == 'action_recommendation':
        response = generate_action_recommendation(conn)
        return response, intent, None
    
    # Step 3: Run deterministic query
    result = execute_query(conn, intent, params)
    
    # Step 4: Generate response
    if has_ai():
        # AI Layer 3: Communication
        if isinstance(result, tuple):
            data_text = "\n".join(df.to_string() if isinstance(df, pd.DataFrame) else str(df) for df in result)
        elif isinstance(result, dict):
            data_text = json.dumps(result, indent=2, default=str)
        elif isinstance(result, pd.DataFrame):
            data_text = result.to_string()
        else:
            data_text = str(result)
        
        ai_response = generate_response_ai(question, data_text, intent)
        if ai_response:
            # For barn alerts, add root cause explanation (AI Layer 4)
            if intent == 'barn_alerts' and isinstance(result, pd.DataFrame) and len(result):
                explanation = explain_barn_alert(conn, result)
                if explanation:
                    ai_response += f"\n\n**Root cause analysis:** {explanation}"
            return ai_response, intent, result
    
    # Fallback: formatted response without AI
    response = format_fallback(intent, result, question)
    return response, intent, result
