"""
SwineIntel — AI Assistant
==========================
Classifies user questions, runs DuckDB queries, and generates
natural-language responses using Claude API.
"""

import duckdb
import pandas as pd
import json
import os

try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False


# ═══════════════════════════════════════════════════════════════════════════════
# QUERY FUNCTIONS — one per intent, all query DuckDB
# ═══════════════════════════════════════════════════════════════════════════════

def get_group_summary(conn, group_id):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.Nursery_Site, l.finisher_site, l.finisher_barn,
               l.Received_Head, l.head_to_finisher, l.nursery_mortality_pct,
               l.days_in_nursery, l.est_market_date, l.Health_Status,
               l.Instrument_Type, l.Coverage_Percent, l.Head_Covered,
               l.effective_floor_cwt, l.unhedged_head, l.Contract_Month,
               h.current_market_cwt, h.pnl_per_cwt, h.pnl_per_head, h.total_pnl,
               l.nursery_avg_temp_f, l.finisher_avg_temp_f,
               l.nursery_temp_alerts, l.finisher_temp_alerts
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        WHERE l.Pig_Group_ID = ?
    """, [group_id]).df()


def get_all_groups(conn):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.finisher_site, l.head_to_finisher,
               l.Instrument_Type, l.Coverage_Percent,
               l.effective_floor_cwt, l.unhedged_pct,
               l.Health_Status, l.est_market_date,
               h.current_market_cwt, h.pnl_per_cwt, h.pnl_per_head, h.total_pnl
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        ORDER BY l.est_market_date
    """).df()


def get_losing_groups(conn):
    return conn.execute("""
        SELECT l.Pig_Group_ID, l.Instrument_Type, l.Coverage_Percent,
               h.hedge_price_cwt, h.current_market_cwt,
               h.pnl_per_head, h.total_pnl
        FROM lifecycle l
        JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        WHERE h.pnl_per_head < 0
        ORDER BY h.total_pnl ASC
    """).df()


def get_best_packer(conn):
    return conn.execute("""
        SELECT * FROM packer_ranking ORDER BY rank
    """).df()


def get_packer_for_group(conn, group_id):
    group = conn.execute("SELECT * FROM lifecycle WHERE Pig_Group_ID = ?", [group_id]).df()
    ranking = conn.execute("SELECT * FROM packer_ranking ORDER BY rank LIMIT 3").df()
    return group, ranking


def get_hedge_comparison(conn, group_id=None):
    lrp = conn.execute("""
        SELECT Commodity, coverageLevelPercent, coveragePrice,
               producerPremiumAmount, "CME Premium", endorsementLength
        FROM lrp_quotes
        WHERE Commodity = 'CME LEAN HOGS'
        ORDER BY coverageLevelPercent DESC
        LIMIT 10
    """).df()
    return lrp


def get_barn_alerts(conn, threshold_pct=15):
    try:
        result = conn.execute("""
            WITH daily_avg AS (
                SELECT Site, Barn, Date, Water_Usage_Gallons,
                       Avg_Temperature_F, Avg_Humidity_pct,
                       AVG(Water_Usage_Gallons) OVER (
                           PARTITION BY Site, Barn
                           ORDER BY Date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                       ) as rolling_avg_water
                FROM barn_env
            )
            SELECT Site, Barn, Date, Water_Usage_Gallons, rolling_avg_water,
                   Avg_Temperature_F, Avg_Humidity_pct,
                   ROUND((1 - Water_Usage_Gallons / NULLIF(rolling_avg_water, 0)) * 100, 1) as water_drop_pct
            FROM daily_avg
            WHERE Water_Usage_Gallons < rolling_avg_water * (1 - ? / 100.0)
              AND rolling_avg_water > 0
            ORDER BY Date DESC
            LIMIT 10
        """, [threshold_pct]).df()
        return result
    except Exception:
        return pd.DataFrame()


def get_feed_cost(conn):
    return conn.execute("""
        SELECT date, corn_cents_bu, sbm_usd_ton,
               feed_cost_usd_ton, feed_cost_per_head
        FROM feed_cost
        ORDER BY date DESC
        LIMIT 30
    """).df()


def get_market_context(conn):
    return conn.execute("""
        SELECT date, he_front_close, national_cash, cme_index, basis
        FROM hog_dashboard
        ORDER BY date DESC
        LIMIT 30
    """).df()


def get_seasonal_context(conn):
    return conn.execute("SELECT * FROM seasonal_avg ORDER BY month").df()


def get_disease_context(conn, group_id):
    group = conn.execute("""
        SELECT Pig_Group_ID, Placement_Date, Health_Status
        FROM lifecycle WHERE Pig_Group_ID = ?
    """, [group_id]).df()
    try:
        disease = conn.execute("SELECT * FROM disease WHERE season = '2024-25' ORDER BY calendar_month").df()
    except Exception:
        disease = pd.DataFrame()
    return group, disease


def get_unhedged_exposure(conn):
    return conn.execute("""
        SELECT Pig_Group_ID, head_to_finisher, unhedged_head, unhedged_pct,
               Instrument_Type, Coverage_Percent, est_market_date
        FROM lifecycle
        WHERE unhedged_pct > 20
        ORDER BY unhedged_head DESC
    """).df()


def get_weekly_summary(conn):
    groups = get_all_groups(conn)
    packer = conn.execute("SELECT * FROM packer_ranking LIMIT 1").df()
    feed = conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 1").df()
    return groups, packer, feed


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def scenario_price_change(conn, delta_cwt):
    """Recalculate all hedge P&Ls with a price shift."""
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
    """Recalculate feed cost at a different corn price."""
    feed = conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 1").df()
    current_corn = feed.corn_cents_bu.values[0]
    current_feed_head = feed.feed_cost_per_head.values[0]

    new_corn_ton = corn_cents_bu / 100 * (2000 / 56)
    new_sbm = feed.sbm_usd_ton.values[0]
    new_feed_ton = 0.75 * new_corn_ton + 0.20 * new_sbm
    new_feed_head = new_feed_ton * (800 / 2000)
    delta = new_feed_head - current_feed_head

    return {
        'current_corn_cents': current_corn,
        'scenario_corn_cents': corn_cents_bu,
        'current_feed_per_head': round(current_feed_head, 2),
        'scenario_feed_per_head': round(new_feed_head, 2),
        'delta_per_head': round(delta, 2),
    }


def scenario_add_hedge(conn, group_id, additional_pct):
    """Show impact of adding hedge coverage to a group."""
    group = conn.execute("SELECT * FROM lifecycle WHERE Pig_Group_ID = ?", [group_id]).df()
    hedge = conn.execute("SELECT * FROM hedge_pnl WHERE Pig_Group_ID = ?", [group_id]).df()
    if len(group) == 0:
        return None

    g = group.iloc[0]
    current_covered = g['Head_Covered']
    current_pct = g['Coverage_Percent']
    total_head = g['head_to_finisher']
    new_pct = min(current_pct + additional_pct / 100, 1.0)
    new_covered = int(total_head * new_pct)
    additional_head = new_covered - current_covered

    current_market = hedge.current_market_cwt.values[0] if len(hedge) else 0
    return {
        'Pig_Group_ID': group_id,
        'current_coverage_pct': round(current_pct * 100, 1),
        'new_coverage_pct': round(new_pct * 100, 1),
        'current_head_covered': int(current_covered),
        'new_head_covered': new_covered,
        'additional_head': additional_head,
        'additional_contracts': additional_head // 400,
        'current_market_cwt': round(current_market, 2),
        'unhedged_after': total_head - new_covered,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INTENT CLASSIFICATION + RESPONSE GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are SwineIntel AI, an expert swine production consultant embedded 
in a hog operation dashboard. You have access to structured data about 18 pig groups, 
their hedge positions, packer settlements, barn conditions, and market data.

When given query results, summarize them in 2-4 sentences like a knowledgeable consultant 
would. Use specific numbers. Be direct and actionable. Don't hedge your language with 
"it appears" — state the facts.

Key context:
- Prices are in $/cwt (hundredweight = 100 lbs of carcass)
- A typical market hog has a 210 lb carcass
- To convert $/cwt to $/head: multiply by 2.10
- Coverage % = fraction of group hedged (higher = more protected)
- Instruments: Futures (lock-in price), Put Option (floor with upside), LRP Policy (subsidized floor)
- The 35% USDA subsidy on LRP makes it cheaper than equivalent CME puts
"""


def classify_intent(question):
    """Rule-based intent classification (no LLM needed, fast + reliable)."""
    q = question.lower().strip()

    # Group-specific
    for pg in [f"pg-{i}" for i in range(1000, 1020)]:
        if pg in q:
            if any(w in q for w in ['packer', 'ship', 'route', 'where']):
                return 'packer_for_group', {'group_id': pg.upper()}
            if any(w in q for w in ['disease', 'prrs', 'ped', 'health risk']):
                return 'disease_context', {'group_id': pg.upper()}
            if 'hedge' in q and any(w in q for w in ['add', 'more', 'increase']):
                pct = 20  # default
                for token in q.split():
                    try:
                        v = float(token.replace('%', ''))
                        if 1 <= v <= 100:
                            pct = v
                    except ValueError:
                        pass
                return 'scenario_add_hedge', {'group_id': pg.upper(), 'pct': pct}
            return 'group_summary', {'group_id': pg.upper()}

    # Scenarios
    if any(w in q for w in ['what if', 'scenario', 'what happens']):
        if 'corn' in q or 'feed' in q:
            price = 500
            for token in q.replace('$', '').split():
                try:
                    v = float(token)
                    if 3 <= v <= 8:
                        price = v * 100
                    elif 300 <= v <= 800:
                        price = v
                except ValueError:
                    pass
            return 'scenario_feed', {'corn_cents': price}
        if any(w in q for w in ['drop', 'fall', 'decline', 'crash', 'down']):
            delta = -5
            for token in q.replace('$', '').replace('-', '').split():
                try:
                    v = float(token)
                    if 1 <= v <= 30:
                        delta = -v
                except ValueError:
                    pass
            return 'scenario_price', {'delta': delta}
        if any(w in q for w in ['rise', 'rally', 'up', 'increase', 'jump']):
            delta = 5
            for token in q.replace('$', '').split():
                try:
                    v = float(token)
                    if 1 <= v <= 30:
                        delta = v
                except ValueError:
                    pass
            return 'scenario_price', {'delta': delta}

    # Packer
    if any(w in q for w in ['packer', 'ship', 'which packer', 'best packer', 'route']):
        return 'best_packer', {}

    # Hedge comparison
    if any(w in q for w in ['lrp', 'put option', 'compare hedge', 'insurance', 'protect']):
        return 'hedge_comparison', {}

    # Barn / health
    if any(w in q for w in ['barn', 'water', 'temperature', 'alert', 'anomal']):
        return 'barn_alerts', {}

    # Feed
    if any(w in q for w in ['feed cost', 'corn price', 'soybean meal']):
        return 'feed_cost', {}

    # Market
    if any(w in q for w in ['market', 'futures', 'price', 'seasonal', 'basis']):
        return 'market_context', {}

    # Losing / risk
    if any(w in q for w in ['losing', 'loss', 'worst', 'risk', 'underwater', 'negative']):
        return 'losing_groups', {}

    # Unhedged
    if any(w in q for w in ['unhedged', 'exposure', 'unprotected', 'open']):
        return 'unhedged_exposure', {}

    # Summary
    if any(w in q for w in ['summary', 'overview', 'how are', 'update', 'weekly']):
        return 'weekly_summary', {}

    # Disease
    if any(w in q for w in ['disease', 'prrs', 'ped', 'outbreak']):
        return 'disease_general', {}

    # Default
    return 'weekly_summary', {}


def execute_query(conn, intent, params):
    """Run the query for the classified intent."""
    handlers = {
        'group_summary':       lambda: get_group_summary(conn, params['group_id']),
        'losing_groups':       lambda: get_losing_groups(conn),
        'best_packer':         lambda: get_best_packer(conn),
        'packer_for_group':    lambda: get_packer_for_group(conn, params['group_id']),
        'hedge_comparison':    lambda: get_hedge_comparison(conn),
        'barn_alerts':         lambda: get_barn_alerts(conn),
        'feed_cost':           lambda: get_feed_cost(conn),
        'market_context':      lambda: get_market_context(conn),
        'unhedged_exposure':   lambda: get_unhedged_exposure(conn),
        'weekly_summary':      lambda: get_weekly_summary(conn),
        'disease_context':     lambda: get_disease_context(conn, params.get('group_id', '')),
        'disease_general':     lambda: conn.execute("SELECT * FROM disease WHERE season='2024-25'").df() if 'disease' in [t[0] for t in conn.execute("SHOW TABLES").fetchall()] else pd.DataFrame(),
        'scenario_price':      lambda: scenario_price_change(conn, params['delta']),
        'scenario_feed':       lambda: scenario_feed_change(conn, params['corn_cents']),
        'scenario_add_hedge':  lambda: scenario_add_hedge(conn, params['group_id'], params['pct']),
    }
    handler = handlers.get(intent, handlers['weekly_summary'])
    return handler()


def generate_response(question, query_result, intent):
    """Generate natural language response. Uses Claude if available, else formats data."""
    # Format query result as text
    if isinstance(query_result, tuple):
        data_text = "\n".join(df.to_string() if isinstance(df, pd.DataFrame) else str(df) for df in query_result)
    elif isinstance(query_result, dict):
        data_text = json.dumps(query_result, indent=2, default=str)
    elif isinstance(query_result, pd.DataFrame):
        data_text = query_result.to_string()
    else:
        data_text = str(query_result)

    if HAS_ANTHROPIC and os.environ.get("ANTHROPIC_API_KEY"):
        client = Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": f"Question: {question}\n\nIntent: {intent}\n\nData:\n{data_text}\n\nProvide a 2-4 sentence consultant-quality summary."
            }]
        )
        return msg.content[0].text
    else:
        # Fallback: format data directly
        return format_fallback(intent, query_result, question)


def format_fallback(intent, result, question):
    """Format response without LLM — still useful."""
    if intent == 'group_summary' and isinstance(result, pd.DataFrame) and len(result):
        r = result.iloc[0]
        pnl_str = f"${r.get('pnl_per_head', 0):+.2f}/head" if pd.notna(r.get('pnl_per_head')) else "N/A"
        return (f"**{r['Pig_Group_ID']}** — {r.get('Instrument_Type', 'N/A')} hedge at "
                f"{r.get('Coverage_Percent', 0):.0%} coverage. "
                f"Health: {r.get('Health_Status', 'N/A')}. "
                f"Hedge P&L: {pnl_str} ({r.get('head_to_finisher', 0):,.0f} head to finisher). "
                f"Nursery mortality: {r.get('nursery_mortality_pct', 0):.1f}%. "
                f"Est. market: {str(r.get('est_market_date', 'N/A'))[:10]}.")

    if intent == 'best_packer' and isinstance(result, pd.DataFrame):
        best = result.iloc[0]
        return (f"Best packer combo: **{best['Packer']} {best['Contract_Type']}** at "
                f"${best['avg_eval_cwt']:.2f}/cwt avg eval price "
                f"(${best['advantage_per_head']:.2f}/head advantage). "
                f"Based on {int(best['loads'])} historical loads.")

    if intent == 'scenario_price' and isinstance(result, pd.DataFrame):
        total = result.scenario_total_pnl.sum()
        winners = (result.scenario_pnl_head > 0).sum()
        return (f"With a ${result.scenario_market.iloc[0] - result.current_market_cwt.iloc[0]:+.0f}/cwt price change: "
                f"total hedge P&L moves to **${total:+,.0f}** across all groups. "
                f"{winners}/{len(result)} groups have positive hedge P&L in this scenario.")

    if intent == 'scenario_feed' and isinstance(result, dict):
        return (f"At corn {result['scenario_corn_cents']:.0f}¢/bu: feed cost = "
                f"**${result['scenario_feed_per_head']:.2f}/head** "
                f"(${result['delta_per_head']:+.2f} vs current ${result['current_feed_per_head']:.2f}).")

    if intent == 'scenario_add_hedge' and isinstance(result, dict) and result:
        return (f"Adding coverage to **{result['Pig_Group_ID']}**: "
                f"{result['current_coverage_pct']:.0f}% → {result['new_coverage_pct']:.0f}% "
                f"(+{result['additional_head']} head = {result['additional_contracts']} contracts). "
                f"Unhedged after: {result['unhedged_after']} head at current market ${result['current_market_cwt']:.2f}/cwt.")

    if intent == 'losing_groups' and isinstance(result, pd.DataFrame):
        if len(result) == 0:
            return "All hedge positions are currently profitable."
        worst = result.iloc[0]
        return (f"{len(result)} groups have negative hedge P&L. Worst: **{worst['Pig_Group_ID']}** "
                f"at ${worst['pnl_per_head']:.2f}/head (${worst['total_pnl']:,.0f} total). "
                f"{worst['Instrument_Type']} hedged at ${worst['hedge_price_cwt']:.2f} vs market ${worst['current_market_cwt']:.2f}.")

    # Generic fallback
    if isinstance(result, pd.DataFrame):
        return f"Found {len(result)} rows. Here's the data:\n\n{result.head(5).to_string()}"
    return str(result)


def ask(conn, question):
    """Main entry point: question → intent → query → response."""
    intent, params = classify_intent(question)
    result = execute_query(conn, intent, params)
    response = generate_response(question, result, intent)
    return response, intent, result
