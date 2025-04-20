import time, datetime
import streamlit as st
import pandas as pd
import stripe
from firebase_admin import credentials, firestore, initialize_app, auth
import firebase_admin
from datetime import datetime, timedelta
import pandas as pd

# ─── AUTHENTICATION ────────────────────────────────────────────────────────────
USERNAME = st.secrets["auth"]["username"]
PASSWORD = st.secrets["auth"]["password"]

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

def login():
    st.title("Admin Login")
    with st.form("login_form", clear_on_submit=True):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")

        if submit:
            if username == USERNAME and password == PASSWORD:
                st.session_state["authenticated"] = True
                st.success("Login successful!")
                st.rerun()
            else:
                st.error("Invalid username or password")

# Check auth before showing dashboard
if not st.session_state["authenticated"]:
    login()
    st.stop()  # Stop rendering the rest of the app

# ─── CONFIG & INITIALIZATION ──────────────────────────────────────────────────
stripe.api_key = st.secrets["STRIPE_SECRET_KEY"]

if not firebase_admin._apps:
    firebase_dict = dict(st.secrets["firebase"])  # Cast AttrDict to plain dict
    cred = credentials.Certificate(firebase_dict)
    initialize_app(cred)
db = firestore.client()

st.set_page_config(
    page_title="PrepVoyage Admin",
    page_icon="./pv_updated_logo.png",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── SIDEBAR ───────────────────────────────────────────────────────────────────
st.sidebar.image("./pv_updated_logo.png", width=60)
st.sidebar.title("Navigation")
page = st.sidebar.radio("", ["Overview", "Pending Ads", "Stripe", "Firebase", "AB Testing"])
days_filter = st.sidebar.slider("Metric window (days)", 7, 60, value=7)

# ─── GLOBAL CSS ────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
      .card {
        padding: 1rem;
        border-radius: 1rem;
        background-color: #f0f2f6;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      }
      .stMetric>div {
        background: none;
      }
    </style>
    """,
    unsafe_allow_html=True
)

# ─── CACHED DATA FUNCTIONS ────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def fetch_payment_data(days):
    """Return a list of simple dicts for all succeeded PaymentIntents in the last `days`."""
    now = int(time.time())
    past = now - days * 86400

    raw = stripe.PaymentIntent.list(created={"gte": past}, limit=100).auto_paging_iter()
    payments = []
    for p in raw:
        if p.status == "succeeded":
            payments.append({
                "amount": p.amount_received / 100,     # dollars
                "created": p.created                   # timestamp
            })
    return payments

@st.cache_data(ttl=300)
def get_total_revenue(days):
    data = fetch_payment_data(days)
    return sum(item["amount"] for item in data)

@st.cache_data(ttl=300)
def get_daily_trend(days):
    data = fetch_payment_data(days)
    trend = {}
    for item in data:
        day = datetime.fromtimestamp(item["created"]).strftime("%Y-%m-%d")
        trend.setdefault(day, 0)
        trend[day] += item["amount"]
    return dict(sorted(trend.items()))

@st.cache_data(ttl=300)
def count_new_users(days):
    threshold = datetime.utcnow() - timedelta(days=days)
    count = 0
    page = auth.list_users()
    while page:
        for user in page.users:
            created = datetime.utcfromtimestamp(user.user_metadata.creation_timestamp / 1000)
            if created >= threshold:
                count += 1
        page = page.get_next_page()
    return count

# 🔹 Build user signup timeline (last 30 days)
@st.cache_data(ttl=300)
def get_user_growth(days=30):
    date_buckets = {
        (datetime.utcnow() - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(days-1, -1, -1)
    }
    page = auth.list_users()
    while page:
        for user in page.users:
            created = datetime.utcfromtimestamp(user.user_metadata.creation_timestamp / 1000)
            date_str = created.strftime("%Y-%m-%d")
            if date_str in date_buckets:
                date_buckets[date_str] += 1
        page = page.get_next_page()
    return date_buckets

# 🔹 Total user count
@st.cache_data(ttl=300)
def get_user_count():
    page = auth.list_users()
    count = 0
    while page:
        count += len(page.users)
        page = page.get_next_page()
    return count

@st.cache_data(ttl=300)
def fetch_pending_ads():
    return [dict(d.to_dict(), id=d.id) for d in db.collection("ads_audit").stream()]

# ─── PAGE: OVERVIEW ────────────────────────────────────────────────────────────
if page == "Overview":
    st.title("PrepVoyage Admin Overview")

    col1, col2, col3 = st.columns(3)
    with col1:
        rev = get_total_revenue(days_filter)
        st.metric("Total Revenue", f"${rev:,.2f}", delta=None)
    with col2:
        users = get_user_count()
        st.metric("Total Users", f"{users:,}", delta=None)
    with col3:
        st.metric("Data Window", f"Last {days_filter} days")

    st.markdown("---")
    st.subheader("Stripe Revenue Data")
    df_trend = pd.DataFrame(list(get_daily_trend(days_filter).items()), columns=["Date","Revenue"])
    st.line_chart(df_trend.set_index("Date"))

# ─── PAGE: PENDING ADS ─────────────────────────────────────────────────────────
elif page == "Pending Ads":
    st.title("Ads Awaiting Moderation")
    ads = fetch_pending_ads()
    if not ads:
        st.info("No pending ads.")
    for ad in ads:
        with st.expander(ad.get("title","Untitled"), expanded=False):
            st.markdown(f"<div class='card'>", unsafe_allow_html=True)
            st.write(ad.get("description",""))
            if ad.get("imageUrl"):
                try:
                    st.image(ad["imageUrl"], use_column_width=True)
                except Exception:
                    st.write("Image failed to load.")

            st.markdown(f"[Visit Ad]({ad.get('link','')})")
            colA, colB = st.columns(2)
            if colA.button("Approve", key=f"app_{ad['id']}"):
                db.collection("ads").document(ad["id"]).set(ad)
                db.collection("ads_audit").document(ad["id"]).delete()
                st.success("Approved!")
            if colB.button("Reject", key=f"rej_{ad['id']}"):
                db.collection("ads_audit").document(ad["id"]).delete()
                st.warning("Rejected")
            st.markdown("</div>", unsafe_allow_html=True)

# ─── PAGE: STRIPE METRICS ───────────────────────────────────────────────────────
elif page == "Stripe":
    st.title("Stripe Metrics")
    st.subheader(f"Last {days_filter} Days")
    col1, col2 = st.columns(2)
    with col1:
        rev = get_total_revenue(days_filter)
        st.metric("Total Revenue", f"${rev:,.2f}")
    with col2:
        df = pd.DataFrame(list(get_daily_trend(days_filter).items()), columns=["Date","Revenue"])
        st.line_chart(df.set_index("Date"))

# ─── PAGE: FIREBASE METRICS ────────────────────────────────────────────────────
elif page == "Firebase":
    st.title("Firebase Users")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Users", f"{get_user_count():,}")
    col2.metric("New (1d)", f"{count_new_users(1):,}")
    col3.metric("New (7d)", f"{count_new_users(7):,}")
    col4.metric("New (30d)", f"{count_new_users(30):,}")

    # Growth chart
    st.markdown("### User Signups Over Time (Last 30 Days)")
    user_growth = get_user_growth(30)
    df_growth = pd.DataFrame(list(user_growth.items()), columns=["Date", "New Users"])
    st.line_chart(df_growth.set_index("Date"))

# ─── PAGE: AB TESTING ──────────────────────────────────────────────────────────
else:
    st.title("AB Testing Results")
    st.subheader("Feature: Gemini Packing List (A) vs Webscraped Packing List (B)")
    colA, colB = st.columns(2)
    with colA:
        avgA = db.collection("ab_testing").where("abGroup","==","A").avg("rating","ratingAvg").get()[0][0].value
        st.metric("Group A Rating", f"{avgA:.2f}/5")
    with colB:
        avgB = db.collection("ab_testing").where("abGroup","==","B").avg("rating","ratingAvg").get()[0][0].value
        st.metric("Group B Rating", f"{avgB:.2f}/5")
            
    st.subheader("Feature: Explore Page (A) vs Linked Items on Packing List (B)")
    colA, colB = st.columns(2)
    with colA:
        avgA = db.collection("Sprint5_ab_testing").where("TestGroup","==","A").avg("rating","ratingAvg").get()[0][0].value
        st.metric("Group A Rating", f"{avgA:.2f}/5")
    with colB:
        avgB = db.collection("Sprint5_ab_testing").where("TestGroup","==","B").avg("rating","ratingAvg").get()[0][0].value
        st.metric("Group B Rating", f"{avgB:.2f}/5")
