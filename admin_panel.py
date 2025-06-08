import streamlit as st
import mysql.connector
from dotenv import load_dotenv
import os

# 🌱 Load environment variables
load_dotenv()

# 🛠 Database config
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

# 🔌 Connection helper
def get_connection():
    return mysql.connector.connect(**db_config)

# 📥 Fetch all deliveries
def fetch_deliveries():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM deliveries ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# 📜 Fetch phone numbers from history
def get_all_phone_numbers():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT phone_number FROM message_history ORDER BY phone_number")
    numbers = [row[0] for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return numbers

# 🧾 Get conversation history
def get_history(phone_number):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT role, message, created_at
        FROM message_history
        WHERE phone_number = %s
        ORDER BY created_at ASC
    """, (phone_number,))
    messages = cursor.fetchall()
    cursor.close()
    conn.close()
    return messages

# 🧠 Define status options
status_options = [
    'pending',
    'awaiting_confirmation',
    'confirmed',
    'correction_requested',
    'waiting_on_correction_details',
    'ready'
]

# 🌐 PAGE TITLE
st.title("💊 PharmacyBot Admin Panel")

# =========================
# SECTION 1: DELIVERIES
# =========================
st.subheader("📦 Current Deliveries")

deliveries = fetch_deliveries()

for delivery in deliveries:
    with st.expander(f"{delivery['patient_name']} ({delivery['status']})"):
        st.write(f"📱 **Phone:** {delivery['phone_number']}")
        st.write(f"📝 **Correction Note:** {delivery['correction_note'] or 'N/A'}")

        # ✍️ Editable fields
        new_address = st.text_input("🏠 Address", value=delivery['delivery_address'], key=f"addr_{delivery['id']}")
        new_time = st.text_input("⏰ Time", value=delivery['delivery_time'], key=f"time_{delivery['id']}")
        new_status = st.selectbox(
            "🚦 Status",
            status_options,
            index=status_options.index(delivery['status']),
            key=f"status_{delivery['id']}"
        )

        if st.button("💾 Save Changes", key=f"save_{delivery['id']}"):
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE deliveries
                SET delivery_address = %s,
                    delivery_time = %s,
                    status = %s
                WHERE id = %s
            """, (new_address, new_time, new_status, delivery['id']))
            conn.commit()
            cursor.close()
            conn.close()
            st.success("✅ Changes saved! Refresh to see update.")

        if st.button("❌ Delete", key=f"delete_{delivery['id']}"):
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM deliveries WHERE id = %s", (delivery['id'],))
            conn.commit()
            cursor.close()
            conn.close()
            st.success("✅ Deleted! Refresh to see update.")

# =========================
# SECTION 2: ADD DELIVERY
# =========================
st.subheader("➕ Add New Delivery")

with st.form("add_delivery_form"):
    name = st.text_input("Patient Name")
    phone = st.text_input("Phone Number (in E.164 format)", placeholder="+1...")
    address = st.text_area("Delivery Address")
    time = st.text_input("Delivery Time")
    submitted = st.form_submit_button("Add Delivery")

    if submitted:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO deliveries (patient_name, phone_number, delivery_address, delivery_time)
            VALUES (%s, %s, %s, %s)
        """, (name, phone, address, time))
        conn.commit()
        cursor.close()
        conn.close()
        st.success("✅ New delivery added! Refresh to see it.")

# =========================
# SECTION 3: MESSAGE HISTORY
# =========================
st.subheader("📜 Message History")

phone_options = get_all_phone_numbers()
selected_number = st.selectbox("Select a phone number to view history:", phone_options)

if selected_number:
    history = get_history(selected_number)
    st.write(f"🧾 Conversation with **{selected_number}**")

    for entry in history:
        if entry['role'] == 'user':
            st.markdown(f"🧑‍⚕️ **User:** {entry['message']}  \n_🕒 {entry['created_at']}_", unsafe_allow_html=True)
        else:
            st.markdown(f"🤖 **Bot:** {entry['message']}  \n_🕒 {entry['created_at']}_", unsafe_allow_html=True)
