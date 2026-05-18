"""
SwineIntel — Dashboard App (Simplified)
=========================================
Progressive disclosure: overview first, depth on demand.
Level 1: 3 metrics + 1 alert (5 seconds)
Level 2: Groups needing attention (30 seconds)
Level 3: Group detail + scenarios + AI chat (2 minutes)

Usage:
    streamlit run app.py
"""

import streamlit as st
import duckdb
import pandas as pd
import numpy as np
from ai_assistant import ask, scenario_price_change, scenario_feed_change
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="SwineIntel", page_icon="🐷", layout="wide")
DB_PATH = "swineintel.duckdb"
NON_FEED_COST_PER_HEAD = 72.00

@st.cache_resource
def get_conn():
    return duckdb.connect(DB_PATH, read_only=True)

conn = get_conn()

# ═══════════════════════════════════════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def load_lifecycle():
    return conn.execute("""
        SELECT l.*, h.current_market_cwt, h.pnl_per_cwt, h.pnl_per_head,
               h.total_pnl, h.symbol as hedge_symbol
        FROM lifecycle l
        LEFT JOIN hedge_pnl h ON l.Pig_Group_ID = h.Pig_Group_ID
        ORDER BY l.est_market_date
    """).df()

@st.cache_data(ttl=300)
def load_packer_ranking():
    return conn.execute("SELECT * FROM packer_ranking ORDER BY rank").df()

@st.cache_data(ttl=300)
def load_feed():
    return conn.execute("SELECT * FROM feed_cost ORDER BY date DESC LIMIT 1").df()

@st.cache_data(ttl=300)
def load_hedge_pnl():
    return conn.execute("SELECT * FROM hedge_pnl").df()

lc = load_lifecycle()
packer_rank = load_packer_ranking()
feed = load_feed()
hedge_pnl = load_hedge_pnl()
feed_per_head = feed.iloc[0].get('feed_cost_per_head', 73.57) if len(feed) else 73.57
total_cost = feed_per_head + NON_FEED_COST_PER_HEAD

# ═══════════════════════════════════════════════════════════════════════════════
# MARGIN + STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def calc_margin(r):
    inst = r.get('Instrument_Type', '')
    hp = r.get('Strike_or_Futures_Price_CWT', 0)
    prem = r.get('Option_or_LRP_Premium_CWT', 0)
    cov = r.get('Coverage_Percent', 0)
    mkt = r.get('current_market_cwt', 0)
    if pd.isna(mkt) or mkt == 0: return None
    if inst == 'Lean Hog Futures':
        blended = (hp * cov) + (mkt * (1 - cov))
    elif inst in ('Lean Hog Put Option', 'LRP Policy'):
        eff = max(mkt, hp) - prem
        blended = (eff * cov) + (mkt * (1 - cov))
    else:
        blended = mkt
    return round(blended * 2.10 - total_cost, 2)

def get_status(margin, cov):
    if pd.isna(margin): return "unknown", "⚪ N/A"
    warn = " ⚠️" if cov < 0.50 else ""
    if margin > 30: return "strong", f"✅ Strong{warn}"
    elif margin > 15: return "on_track", f"✅ On track{warn}"
    elif margin > 5: return "watch", f"🟡 Watch{warn}"
    elif margin > 0: return "tight", f"🟡 Tight{warn}"
    else: return "at_risk", f"🔴 At risk{warn}"

lc['est_margin'] = lc.apply(calc_margin, axis=1)
lc['status_key'], lc['status_label'] = zip(*lc.apply(
    lambda r: get_status(r['est_margin'], r.get('Coverage_Percent', 0)), axis=1))

needs_attention = lc[
    (lc['Coverage_Percent'] < 0.55) | (lc['est_margin'] < 30) |
    (lc['Health_Status'].isin(['Positive', 'Monitored']))
].copy()

# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 1 — OVERVIEW (5 seconds)
# ═══════════════════════════════════════════════════════════════════════════════

st.title("🐷 SwineIntel")
st.caption(f"{len(lc)} active groups · {lc.head_to_finisher.sum():,.0f} total head")

col1, col2, col3 = st.columns(3)
with col1:
    avg_m = lc.est_margin.mean()
    st.metric("Operation margin", f"${avg_m:+.2f}/head",
              delta=f"${avg_m * lc.head_to_finisher.sum():+,.0f} total est. profit",
              delta_color="normal" if avg_m >= 0 else "inverse")
with col2:
    low_cov = (lc.Coverage_Percent < 0.55).sum()
    tight = (lc.est_margin < 30).sum()
    st.metric("Groups needing action", f"{len(needs_attention)}",
              delta=f"{low_cov} low coverage · {tight} tight margin", delta_color="off")
with col3:
    if len(packer_rank):
        bp = packer_rank.iloc[0]
        st.metric("Packer opportunity", f"${bp['advantage_per_head']:.2f}/head",
                  delta=f"{bp['Packer']} {bp['Contract_Type']}", delta_color="off")

# Alert
try:
    alert = conn.execute("""
        WITH d AS (
            SELECT Site, Barn, Date, Water_Usage_Gallons,
                   AVG(Water_Usage_Gallons) OVER (
                       PARTITION BY Site, Barn ORDER BY Date ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING
                   ) as avg14
            FROM barn_env)
        SELECT Site, Barn, Date, Water_Usage_Gallons, avg14,
               ROUND((1 - Water_Usage_Gallons/NULLIF(avg14,0))*100, 1) as drop_pct
        FROM d WHERE Water_Usage_Gallons < avg14*0.85 AND avg14>0
        ORDER BY Date DESC LIMIT 1
    """).df()
    if len(alert):
        a = alert.iloc[0]
        st.warning(f"🌡️ **Barn {a['Barn']}** at {a['Site']}: water down {a['drop_pct']:.0f}% "
                   f"on {str(a['Date'])[:10]}. Recommend vet inspection.")
except Exception:
    pass

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 2 — GROUPS NEEDING ATTENTION (30 seconds)
# ═══════════════════════════════════════════════════════════════════════════════

st.subheader(f"Groups needing attention ({len(needs_attention)} of {len(lc)})")

if len(needs_attention):
    disp = needs_attention[['Pig_Group_ID','head_to_finisher','Coverage_Percent',
                             'est_margin','Health_Status','status_label']].copy()
    disp.columns = ['Group','Head','Coverage','Margin/Head','Health','Status']
    disp['Coverage'] = (disp['Coverage']*100).round(0).astype(int).astype(str) + '%'
    disp['Margin/Head'] = disp['Margin/Head'].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    st.dataframe(disp, use_container_width=True, hide_index=True)
else:
    st.success("All groups on track.")

with st.expander(f"View all {len(lc)} groups"):
    full = lc[['Pig_Group_ID','finisher_site','head_to_finisher','Health_Status',
                'Instrument_Type','Coverage_Percent','effective_floor_cwt',
                'current_market_cwt','pnl_per_head','est_margin','est_market_date','status_label']].copy()
    full.columns = ['Group','Site','Head','Health','Instrument','Coverage',
                     'Floor','Market','Hedge P&L/Hd','Margin/Hd','Est Market','Status']
    full['Coverage'] = (full['Coverage']*100).round(0).astype(int).astype(str) + '%'
    full['Floor'] = full['Floor'].round(2)
    full['Market'] = full['Market'].round(2)
    full['Hedge P&L/Hd'] = full['Hedge P&L/Hd'].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    full['Margin/Hd'] = full['Margin/Hd'].apply(lambda x: f"${x:+.2f}" if pd.notna(x) else "N/A")
    full['Est Market'] = pd.to_datetime(full['Est Market']).dt.strftime('%Y-%m-%d')
    st.dataframe(full, use_container_width=True, hide_index=True)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 3 — DETAIL + AI (side by side)
# ═══════════════════════════════════════════════════════════════════════════════

detail_col, ai_col = st.columns([1, 1])

with detail_col:
    st.subheader("🔍 Group detail")
    group_id = st.selectbox("Select group", lc.Pig_Group_ID.tolist(), index=0, label_visibility="collapsed")

    if group_id:
        g = lc[lc.Pig_Group_ID == group_id].iloc[0]
        h = hedge_pnl[hedge_pnl.Pig_Group_ID == group_id]
        margin = g.get('est_margin', 0)
        _, status_text = get_status(margin, g.get('Coverage_Percent', 0))

        st.markdown(f"### {group_id} — {status_text}")

        m1, m2 = st.columns(2)
        with m1:
            st.metric("Est. margin", f"${margin:+.2f}/head")
            st.metric("Head to finisher", f"{g.get('head_to_finisher',0):,.0f}")
            st.metric("Health", g.get('Health_Status', 'N/A'))
        with m2:
            pnl = h.pnl_per_head.values[0] if len(h) else 0
            st.metric("Hedge P&L", f"${pnl:+.2f}/head" if pd.notna(pnl) else "N/A")
            st.metric("Coverage", f"{g.get('Coverage_Percent',0):.0%}")
            st.metric("Instrument", g.get('Instrument_Type', 'N/A'))

        # Packer recommendation
        if len(packer_rank):
            best = packer_rank.iloc[0]
            savings = best['advantage_per_head'] * g.get('head_to_finisher', 0)
            st.info(f"🏭 **Ship to {best['Packer']} {best['Contract_Type']}** — "
                    f"${best['advantage_per_head']:.2f}/head (est. ${savings:,.0f} for this group)")

        # Expandable sections
        with st.expander("🌡️ Day-to-day monitoring"):
            st.caption("Nick's daily checklist — barn conditions during this group's stay")
            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                st.caption("**Temperature**")
                if pd.notna(g.get('nursery_avg_temp_f')):
                    st.metric("Nursery avg", f"{g['nursery_avg_temp_f']:.1f}°F",
                              delta=f"{int(g.get('nursery_temp_alerts', 0))} cold alerts", delta_color="off")
                if pd.notna(g.get('finisher_avg_temp_f')):
                    st.metric("Finisher avg", f"{g['finisher_avg_temp_f']:.1f}°F",
                              delta=f"{int(g.get('finisher_temp_alerts', 0))} cold alerts", delta_color="off")
            with bc2:
                st.caption("**Water & humidity**")
                if pd.notna(g.get('nursery_avg_water')):
                    st.metric("Nursery water", f"{g['nursery_avg_water']:,.0f} gal/day")
                if pd.notna(g.get('nursery_avg_humidity')):
                    st.metric("Nursery humidity", f"{g['nursery_avg_humidity']:.0f}%")
                if pd.notna(g.get('finisher_avg_water')):
                    st.metric("Finisher water", f"{g['finisher_avg_water']:,.0f} gal/day")
            with bc3:
                st.caption("**Outdoor (NOAA)**")
                try:
                    weather = conn.execute(f"""
                        SELECT AVG(outdoor_high_f) as avg_high, AVG(outdoor_low_f) as avg_low,
                               MAX(peak_wind_gust_mph) as max_gust, AVG(avg_wind_mph) as avg_wind
                        FROM weather WHERE date >= '{g['Placement_Date']}' AND date <= '{g['est_market_date']}'
                    """).df()
                    if len(weather) and pd.notna(weather.iloc[0]['avg_high']):
                        w = weather.iloc[0]
                        st.metric("Avg outdoor high", f"{w['avg_high']:.0f}°F")
                        st.metric("Avg outdoor low", f"{w['avg_low']:.0f}°F")
                        st.metric("Peak wind gust", f"{w['max_gust']:.0f} mph")
                except Exception:
                    st.caption("Weather data not available")

            # Feed consumption chart
            try:
                feed_data = conn.execute(f"""
                    SELECT day_in_finisher, actual_feed_per_head_lb, expected_feed_per_head_lb
                    FROM feed_consumption WHERE pig_group_id = '{group_id}'
                    ORDER BY day_in_finisher
                """).df()
                if len(feed_data):
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(x=feed_data.day_in_finisher, y=feed_data.expected_feed_per_head_lb,
                                            name='Expected (ISU model)', line=dict(dash='dash', color='gray')))
                    fig.add_trace(go.Scatter(x=feed_data.day_in_finisher, y=feed_data.actual_feed_per_head_lb,
                                            name='Actual', line=dict(color='#1f77b4')))
                    fig.update_layout(height=250, margin=dict(t=30, b=30, l=40, r=10),
                                      title="Feed intake (lb/head/day)", xaxis_title="Day in finisher",
                                      yaxis_title="lb/head/day", plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption("Source: estimated from ISU growth model (synthetic)")
            except Exception:
                pass

        with st.expander("📋 Weekly mortality trend"):
            try:
                mort = conn.execute(f"""
                    SELECT week_number, deaths, cumulative_deaths, head_remaining, weekly_mortality_pct
                    FROM weekly_mortality WHERE pig_group_id = '{group_id}'
                    ORDER BY week_number
                """).df()
                if len(mort):
                    import plotly.graph_objects as go
                    fig = go.Figure()
                    fig.add_trace(go.Bar(x=mort.week_number, y=mort.deaths, name='Deaths',
                                        marker_color='#d62728'))
                    fig.add_trace(go.Scatter(x=mort.week_number, y=mort.head_remaining, name='Head remaining',
                                            yaxis='y2', line=dict(color='#2ca02c')))
                    fig.update_layout(height=250, margin=dict(t=30, b=30, l=40, r=40),
                                      title="Nursery mortality by week",
                                      xaxis_title="Week", yaxis_title="Deaths",
                                      yaxis2=dict(title="Head remaining", overlaying='y', side='right'),
                                      plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(fig, use_container_width=True)
                    total_mort = mort.deaths.sum()
                    st.caption(f"Total nursery mortality: {total_mort} head. Source: distributed from pig_flow totals.")
            except Exception:
                pass

        with st.expander("🔍 Investigation mode"):
            st.caption("Deep-dive data for problem diagnosis (per Nick's workflow)")

            # Treatment logs
            try:
                treats = conn.execute(f"""
                    SELECT date, treatment_type, medication, dose, administered_by, reason, head_treated
                    FROM treatment_logs WHERE pig_group_id = '{group_id}'
                    ORDER BY date
                """).df()
                if len(treats):
                    st.markdown("**Treatment logs**")
                    st.dataframe(treats, use_container_width=True, hide_index=True)
                else:
                    st.markdown("**Treatment logs** — No treatments recorded (Health: Negative)")
            except Exception:
                st.markdown("**Treatment logs** — *placeholder: requires PigCHAMP integration*")

            # Feed delivery
            try:
                barn_name = g.get('finisher_barn', g.get('Nursery_Barn', ''))
                site_name = g.get('finisher_site', g.get('Nursery_Site', ''))
                deliveries = conn.execute(f"""
                    SELECT delivery_date, feed_type, tons_delivered, driver, lot_number,
                           bin_level_before_pct, bin_level_after_pct
                    FROM feed_delivery WHERE site = '{site_name}'
                    ORDER BY delivery_date DESC LIMIT 5
                """).df()
                if len(deliveries):
                    st.markdown(f"**Recent feed deliveries** ({site_name})")
                    st.dataframe(deliveries, use_container_width=True, hide_index=True)
                    st.caption("Source: SYNTHETIC — requires bin scale API")
            except Exception:
                st.markdown("**Feed deliveries** — *placeholder: requires bin scale API*")

            # Employee entry
            try:
                entries = conn.execute(f"""
                    SELECT date, employee_name, role, entry_time, exit_time, purpose
                    FROM employee_entry WHERE barn = '{barn_name}'
                    ORDER BY date DESC, entry_time DESC LIMIT 10
                """).df()
                if len(entries):
                    st.markdown(f"**Recent barn entries** ({barn_name})")
                    st.dataframe(entries, use_container_width=True, hide_index=True)
                    st.caption("Source: SYNTHETIC — requires badge access system")
            except Exception:
                st.markdown("**Employee entry logs** — *placeholder: requires badge system*")

        with st.expander("🦠 Disease context"):
            try:
                disease = conn.execute(
                    "SELECT * FROM disease WHERE season='2024-25' AND disease='PRRS' ORDER BY calendar_month").df()
                if len(disease):
                    pm = pd.to_datetime(g['Placement_Date']).month if pd.notna(g.get('Placement_Date')) else None
                    if pm:
                        match = disease[disease.calendar_month == pm]
                        if len(match):
                            prrs = match.cumulative_incidence_pct.values[0]
                            end_m = min(pm + 2, 12)
                            end = disease[disease.calendar_month == end_m]
                            delta = end.cumulative_incidence_pct.values[0] - prrs if len(end) else None
                            st.metric("PRRS at placement", f"{prrs:.1f}%")
                            if delta:
                                risk = "HIGH" if delta > 3 else "MODERATE" if delta > 1.5 else "LOW"
                                st.metric("During nursery", f"+{delta:.1f}% new infections", delta=risk, delta_color="off")
                            st.caption("Source: MSHMP 2024-25 (U. of Minnesota)")
            except Exception:
                st.caption("Disease data not available")

        with st.expander("🛡️ Hedge comparison (LRP vs CME put)"):
            try:
                lrp = conn.execute("""
                    SELECT coverageLevelPercent as "Coverage", coveragePrice as "Price $/cwt",
                           ROUND(perHeadPremium/2.10, 2) as "LRP $/cwt",
                           "CME Premium" as "CME Put $/cwt", endorsementLength as "Weeks"
                    FROM lrp_quotes WHERE Commodity='CME LEAN HOGS' AND coverageLevelPercent>=0.90
                    ORDER BY coverageLevelPercent DESC LIMIT 6
                """).df()
                if len(lrp):
                    lrp['Savings'] = (lrp['CME Put $/cwt'] - lrp['LRP $/cwt']).round(2)
                    st.dataframe(lrp.round(2), use_container_width=True, hide_index=True)
            except Exception:
                st.caption("LRP data not available")

# ── AI ASSISTANT ────────────────────────────────────────────────────────────

with ai_col:
    st.subheader("🤖 AI assistant")

    if "messages" not in st.session_state:
        opening = "**Weekly insight**\n\n"
        if len(lc):
            best_g = lc.sort_values('est_margin', ascending=False).iloc[0]
            worst_g = lc.sort_values('est_margin').iloc[0]
            opening += f"Strongest: **{best_g.Pig_Group_ID}** (${best_g.est_margin:+.2f}/head). "
            opening += f"Weakest: **{worst_g.Pig_Group_ID}** (${worst_g.est_margin:+.2f}/head).\n\n"
        low = lc[lc.Coverage_Percent < 0.55]
        if len(low):
            opening += f"⚠️ {len(low)} groups below 55% coverage: {', '.join(low.Pig_Group_ID.tolist())}.\n\n"
        if len(packer_rank):
            bp = packer_rank.iloc[0]
            opening += f"🏭 Best packer: {bp['Packer']} {bp['Contract_Type']} (${bp['advantage_per_head']:.2f}/head advantage)."
        st.session_state.messages = [{"role": "assistant", "content": opening}]

    chat = st.container(height=380)
    with chat:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Scenario buttons
    st.caption("Quick scenarios")
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        if st.button("🔻 Hogs -$5", use_container_width=True):
            r = scenario_price_change(conn, -5)
            t, c = r.scenario_total_pnl.sum(), r.total_pnl.sum()
            msg = f"**Hogs -$5:** Hedge P&L ${c:+,.0f} → ${t:+,.0f} (${t-c:+,.0f}). {(r.scenario_pnl_head>0).sum()}/{len(r)} groups positive."
            st.session_state.messages.append({"role": "assistant", "content": msg}); st.rerun()
    with b2:
        if st.button("🔺 Hogs +$5", use_container_width=True):
            r = scenario_price_change(conn, 5)
            t, c = r.scenario_total_pnl.sum(), r.total_pnl.sum()
            msg = f"**Hogs +$5:** Hedge P&L ${c:+,.0f} → ${t:+,.0f} (${t-c:+,.0f}). {(r.scenario_pnl_head<0).sum()}/{len(r)} negative (missed upside)."
            st.session_state.messages.append({"role": "assistant", "content": msg}); st.rerun()
    with b3:
        if st.button("🌽 Corn $5", use_container_width=True):
            r = scenario_feed_change(conn, 500)
            imp = r['delta_per_head'] * lc.head_to_finisher.sum()
            msg = f"**Corn $5.00:** Feed ${r['current_feed_per_head']:.2f} → ${r['scenario_feed_per_head']:.2f}/head (${r['delta_per_head']:+.2f}). Total: ${imp:+,.0f}."
            st.session_state.messages.append({"role": "assistant", "content": msg}); st.rerun()
    with b4:
        if st.button("🏭 Packers", use_container_width=True):
            if len(packer_rank):
                msg = "**Packer ranking:**\n"
                for i, (_, r) in enumerate(packer_rank.head(3).iterrows()):
                    msg += f"\n{i+1}. {r['Packer']} {r['Contract_Type']} — ${r['avg_eval_cwt']:.2f}/cwt (${r['advantage_per_head']:.2f}/head)"
                msg += f"\n\nSpread: **${packer_rank.iloc[0]['advantage_per_head']:.2f}/head**"
            else:
                msg = "No packer data."
            st.session_state.messages.append({"role": "assistant", "content": msg}); st.rerun()

    if prompt := st.chat_input("Ask anything..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.spinner("Analyzing..."):
            response, intent, result = ask(conn, prompt)
        st.session_state.messages.append({"role": "assistant", "content": response})
        st.rerun()

# Footer
st.divider()
st.caption("SwineIntel · Pro Ag Analytics Platform · Syracuse CCDS 2026")
st.caption("Data: Pro Ag (Track 2) · CME/USDA (Track 1) · NOAA (weather) · MSHMP (disease)")
