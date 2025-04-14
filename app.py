import os
import time
import datetime
import streamlit as st
import pandas as pd
import stripe
from dotenv import load_dotenv
from firebase_admin import credentials, firestore, initialize_app
import firebase_admin

# Load environment variables
load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Initialize Firebase Admin
if not firebase_admin._apps:
    cred = credentials.Certificate("serviceAccountKey.json")  # Path to your service account key
    initialize_app(cred)
db = firestore.client()

# 🔹 Fetch pending ads
def fetch_pending_ads():
    ads_ref = db.collection("ads_audit")
    return [dict(doc.to_dict(), id=doc.id) for doc in ads_ref.stream()]

# 🔹 Refund a Stripe payment
def refund_payment(payment_intent_id):
    try:
        refund = stripe.Refund.create(payment_intent=payment_intent_id)
        return f"Refunded: {refund['id']}"
    except Exception as e:
        return f"Refund error: {str(e)}"

# 🔹 Approve ad: move to production
def approve_ad(ad):
    db.collection("ads").document(ad["id"]).set(ad)
    db.collection("ads_audit").document(ad["id"]).delete()

# 🔹 Reject ad: mark rejected and refund
def reject_ad(ad):
    db.collection("ads_audit").document(ad["id"]).delete()
    return "🗑️ Ad deleted from audit queue."

# 🔹 Total revenue from successful payments (last N days)
def get_total_revenue(days=30):
    now = int(time.time())
    past = now - days * 86400
    payments = stripe.PaymentIntent.list(created={"gte": past}, limit=100)
    total = sum(p.amount_received for p in payments.auto_paging_iter() if p.status == "succeeded")
    return total / 100  # Convert from cents to dollars

# 🔹 Daily trend of successful payments (for charting)
def get_daily_trend(days=7):
    now = int(time.time())
    past = now - days * 86400
    payments = stripe.PaymentIntent.list(created={"gte": past}, limit=100)
    trend = {}
    for p in payments.auto_paging_iter():
        if p.status != "succeeded":
            continue
        day = datetime.datetime.fromtimestamp(p.created).strftime("%Y-%m-%d")
        trend[day] = trend.get(day, 0) + p.amount_received / 100
    return trend

# Streamlit UI
st.set_page_config(page_title="Admin Dashboard", layout="wide")
st.title("PrepVoyage Admin Dashboard")

# Pending ads
ads = fetch_pending_ads()
if not ads:
    st.info("No pending ads in audit queue.")
else:
    st.subheader("Pending Ads for Moderation")

for ad in ads:
    with st.expander(f"{ad.get('title', 'Untitled')}"):
        st.write(ad.get("description", ""))
        st.markdown(f"[Visit Link]({ad.get('link', '')})")

        # Try showing the image (with fallback)
        image_url = ad.get("imageUrl")
        if image_url:
            try:
                st.image(image_url, width=500)
            except Exception:
                st.warning("⚠️ Could not load ad image.")

        # Approve / Reject buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.button(f"Approve {ad['id']}"):
                approve_ad(ad)
                st.success(f"Ad {ad['id']} approved!")

        with col2:
            if st.button(f"Reject {ad['id']}"):
                refund_result = reject_ad(ad)
                st.warning(refund_result)


# Stripe metrics
st.markdown("## Stripe Metrics (Last 7 Days)")

revenue = get_total_revenue(days=7)
st.metric("Total Revenue", f"${revenue:,.2f}")

trend = get_daily_trend(days=7)
if trend:
    df_trend = pd.DataFrame(list(trend.items()), columns=["Date", "Revenue ($)"])
    st.line_chart(df_trend.set_index("Date"))
else:
    st.info("No successful payments in the last 7 days.")
