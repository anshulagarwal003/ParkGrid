import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(
    page_title="Find Parking – Boston",
    page_icon="🅿️",
    layout="centered",
)

# ── Custom CSS ──────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* dark background */
.stApp { background-color: #0f1117; color: #fff; }

/* hide streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 2.5rem; padding-bottom: 4rem; max-width: 640px; }

/* ── header badge ── */
.loc-badge {
    display: inline-flex; align-items: center; gap: 6px;
    color: #4ade80; font-size: 13px; font-weight: 600;
    letter-spacing: .1em; text-transform: uppercase; margin-bottom: 4px;
}
.page-title { font-size: 38px; font-weight: 800; margin-bottom: 4px; color: #fff; }
.page-sub   { color: #9ca3af; font-size: 15px; margin-bottom: 28px; }

/* ── input card ── */
.input-card {
    background: #161b27;
    border: 1px solid rgba(255,255,255,.08);
    border-radius: 20px;
    padding: 28px 24px;
    margin-bottom: 24px;
}
.section-label {
    font-size: 11px; font-weight: 600; color: #6b7280;
    letter-spacing: .08em; text-transform: uppercase; margin-bottom: 8px;
}

/* override streamlit input styling */
input[type="number"], input[type="text"] {
    background: #1e2435 !important;
    border: 1px solid rgba(255,255,255,.1) !important;
    border-radius: 12px !important;
    color: #fff !important;
    font-size: 15px !important;
}

/* ── spot card ── */
.spot-card {
    background: #161b27;
    border: 1px solid rgba(255,255,255,.07);
    border-radius: 16px;
    padding: 18px 20px;
    margin-bottom: 12px;
    transition: border-color .15s;
}
.spot-card:hover { border-color: rgba(74,222,128,.35); }

.spot-top {
    display: flex; align-items: flex-start;
    justify-content: space-between; margin-bottom: 10px;
}
.spot-rank {
    min-width: 28px; height: 28px; border-radius: 8px;
    background: rgba(74,222,128,.15); color: #4ade80;
    font-size: 13px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    margin-right: 12px; flex-shrink: 0;
}
.spot-name  { font-size: 16px; font-weight: 700; flex: 1; line-height: 1.3; color: #fff; }
.spot-dist  { font-size: 13px; color: #4ade80; font-weight: 600; white-space: nowrap; margin-left: 10px; }

.tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.tag {
    font-size: 11px; font-weight: 600; padding: 4px 10px;
    border-radius: 6px; letter-spacing: .03em;
}
.tag-rate  { background: rgba(74,222,128,.12); color: #4ade80;  border: 1px solid rgba(74,222,128,.22); }
.tag-time  { background: rgba(96,165,250,.10); color: #60a5fa;  border: 1px solid rgba(96,165,250,.22); }
.tag-max   { background: rgba(251,146,60,.10); color: #fb923c;  border: 1px solid rgba(251,146,60,.22); }
.tag-slots  { background: rgba(250,204,21,.10);  color: #facc15;  border: 1px solid rgba(250,204,21,.22); }

/* ── multi-stop banner ── */
.multi-banner {
    background: #161b27; border: 1px solid rgba(74,222,128,.2);
    border-radius: 16px; padding: 16px 20px;
    display: flex; align-items: center; gap: 16px; margin-bottom: 16px;
}
.multi-banner-icon {
    width: 40px; height: 40px; border-radius: 50%;
    background: rgba(74,222,128,.15); color: #4ade80;
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; flex-shrink: 0;
}
.multi-banner-text { flex: 1; }
.multi-banner-text b { display: block; color: #fff; font-size: 14px; font-weight: 700; margin-bottom: 2px; }
.multi-banner-text span { color: #6b7280; font-size: 13px; }
.multi-banner-pill {
    background: rgba(74,222,128,.15); color: #4ade80;
    border-radius: 20px; padding: 4px 12px; font-size: 13px; font-weight: 700;
    white-space: nowrap;
}
.multi-nav-btn {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    width: 100%; padding: 13px;
    background: #4ade80; border: none;
    border-radius: 12px; color: #0f1117; font-size: 15px; font-weight: 700;
    text-decoration: none; cursor: pointer; margin-bottom: 16px;
}
.multi-nav-btn:hover { background: #86efac; color: #0f1117; }

.spot-free { font-size: 12px; color: #6b7280; line-height: 1.6; margin-bottom: 10px; }
.spot-free b { color: #9ca3af; }

.nav-btn {
    display: flex; align-items: center; justify-content: center; gap: 8px;
    width: 100%; padding: 11px;
    background: rgba(74,222,128,.1); border: 1px solid rgba(74,222,128,.22);
    border-radius: 10px; color: #4ade80; font-size: 14px; font-weight: 600;
    text-decoration: none; cursor: pointer;
}
.nav-btn:hover { background: rgba(74,222,128,.18); color: #4ade80; }

/* ── no results ── */
.no-results {
    text-align: center; padding: 60px 20px;
    color: #6b7280; font-size: 15px;
}
.no-results h3 { color: #9ca3af; font-size: 20px; margin-bottom: 8px; }

/* ── pill metrics ── */
.metrics-row { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.metric-pill {
    background: #161b27; border: 1px solid rgba(255,255,255,.08);
    border-radius: 10px; padding: 10px 16px;
    font-size: 13px; color: #9ca3af; flex: 1; min-width: 100px; text-align: center;
}
.metric-pill b { display: block; font-size: 20px; font-weight: 700; color: #fff; margin-bottom: 2px; }
</style>
""", unsafe_allow_html=True)


# ── Load data ───────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    df = pd.read_csv("parking_grouped_count.csv")
    df = df.dropna(subset=["LATITUDE", "LONGITUDE"])
    df["LATITUDE"]   = df["LATITUDE"].astype(float)
    df["LONGITUDE"]  = df["LONGITUDE"].astype(float)
    df["BASE_RATE"]  = pd.to_numeric(df["BASE_RATE"], errors="coerce").fillna(0)
    df["PAY_MAX_MINS"] = pd.to_numeric(df["PAY_MAX_MINS"], errors="coerce").fillna(0)
    df["SLOT_COUNT"]   = pd.to_numeric(df["SLOT_COUNT"],   errors="coerce").fillna(0).astype(int)
    return df


def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat = np.radians(lat2 - lat1)
    dlng = np.radians(lng2 - lng1)
    a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1))*np.cos(np.radians(lat2))*np.sin(dlng/2)**2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))


def fmt_dist(km):
    return f"{int(km*1000)}m" if km < 1 else f"{km:.2f} km"

def fmt_max(mins):
    if not mins: return None
    return f"{int(mins)//60}hr max" if mins >= 60 else f"{int(mins)}min max"


# ── Header ──────────────────────────────────────────────────────────────────
st.markdown("""
<div class="loc-badge">📍 Boston, MA</div>
<div class="page-title">Find Parking</div>
<div class="page-sub">Enter your location to find the 10 closest individual spots</div>
""", unsafe_allow_html=True)


# ── Input card ──────────────────────────────────────────────────────────────
with st.container():
    st.markdown('<div class="input-card">', unsafe_allow_html=True)

    st.markdown('<div class="section-label">📌 Your location</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        lat = st.number_input("Latitude",  value=42.3601, format="%.6f", step=0.0001)
    with col2:
        lng = st.number_input("Longitude", value=-71.0589, format="%.6f", step=0.0001)

    st.markdown('<div class="section-label" style="margin-top:16px">🔍 Search radius</div>', unsafe_allow_html=True)
    radius_km = st.select_slider(
        "radius", options=[0.25, 0.5, 1.0, 2.0, 5.0],
        value=1.0, label_visibility="collapsed",
        format_func=lambda x: f"{int(x*1000)}m" if x < 1 else f"{x:.0f} km"
    )

    search = st.button("🅿️  Find Parking Spots", use_container_width=True, type="primary")
    st.markdown('</div>', unsafe_allow_html=True)


# ── Search ──────────────────────────────────────────────────────────────────
if search:
    df = load_data()
    df = df.copy()
    df["dist_km"] = haversine(lat, lng, df["LATITUDE"].values, df["LONGITUDE"].values)
    nearby = df[df["dist_km"] <= radius_km].sort_values("dist_km").head(9).reset_index(drop=True)

    # summary pills
    st.markdown(f"""
    <div class="metrics-row">
      <div class="metric-pill"><b>{len(nearby)}</b>spots found</div>
      <div class="metric-pill"><b>{radius_km} km</b>search radius</div>
      <div class="metric-pill"><b>${nearby['BASE_RATE'].mean():.2f}</b>avg $/hr</div>
    </div>
    """, unsafe_allow_html=True)

    if nearby.empty:
        st.markdown("""
        <div class="no-results">
          <h3>No spots nearby</h3>
          Try increasing the search radius or check a different location.
        </div>
        """, unsafe_allow_html=True)
    else:
        # Build multi-stop Google Maps URL — no hardcoded origin so Maps uses live location
        waypoints = "/".join(
            f"{row['LATITUDE']},{row['LONGITUDE']}"
            for _, row in nearby.iterrows()
        )
        multi_url = f"https://www.google.com/maps/dir/Current+Location/{waypoints}"

        st.markdown(f"""
        <div class="multi-banner">
          <div class="multi-banner-icon">⬆</div>
          <div class="multi-banner-text">
            <b>Multi-stop route ready</b>
            <span>Google Maps will navigate all {len(nearby)} stops in order</span>
          </div>
          <span class="multi-banner-pill">~{len(nearby)} stops</span>
        </div>
        <a class="multi-nav-btn" href="{multi_url}" target="_blank">
          ➤ &nbsp; Navigate All Locations in Google Maps
        </a>
        """, unsafe_allow_html=True)

        for i, row in nearby.iterrows():
            max_t  = fmt_max(row["PAY_MAX_MINS"])
            dist_s = fmt_dist(row["dist_km"])
            maps_url = f"https://www.google.com/maps/dir/?api=1&destination={row['LATITUDE']},{row['LONGITUDE']}"
            max_tag  = f'<span class="tag tag-max">{max_t}</span>' if max_t else ""
            slot_tag = f'<span class="tag tag-slots">🅿️ {row["SLOT_COUNT"]} slots</span>'

            st.markdown(f"""
            <div class="spot-card">
              <div class="spot-top">
                <div class="spot-rank">#{i+1}</div>
                <div class="spot-name">{row['STREET']}</div>
                <div class="spot-dist">{dist_s}</div>
              </div>
              <div class="tags">
                <span class="tag tag-rate">{row['PAY_RATE']}/hr</span>
                <span class="tag tag-time">{row['PAY_HOURS']} · {row['PAY_DAYS']}</span>
                {max_tag}
                {slot_tag}
              </div>
              <div class="spot-free"><b>Free when:</b> {row['PARK_NO_PAY']}</div>
              <a class="nav-btn" href="{maps_url}" target="_blank">
                ➤ &nbsp; Navigate in Google Maps
              </a>
            </div>
            """, unsafe_allow_html=True)
