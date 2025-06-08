import mysql.connector
from twilio.rest import Client
from dotenv import load_dotenv
import os

# Load secrets from .env file
load_dotenv()

# Twilio config
twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

# DB config
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

# Connect to MySQL
db = mysql.connector.connect(**db_config)
cursor = db.cursor(dictionary=True)

# Get the next pending delivery
cursor.execute("""
    SELECT * FROM deliveries
    WHERE status = 'pending'
    ORDER BY created_at ASC
    LIMIT 1;
""")
delivery = cursor.fetchone()

if delivery:
    # Prepare message
    body = f"Hi {delivery['patient_name']}, we’re delivering your medication to:\n{delivery['delivery_address']} at {delivery['delivery_time']}.\nReply YES to confirm or NO if anything is wrong."

    # Send SMS
    client = Client(twilio_sid, twilio_token)
    client.messages.create(
        body=body,
        from_=twilio_number,
        to=delivery['phone_number']
    )

    # Update status to avoid re-sending
    # Update status to avoid re-sending
    cursor.execute("""
        UPDATE deliveries
        SET status = 'awaiting_confirmation'
        WHERE id = %s
    """, (delivery['id'],))

    db.commit()
    print("Message sent and status updated.")
else:
    print("No pending deliveries.")







