from flask import Flask, request, abort
from openai import OpenAI
import mysql.connector
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator
import os
import json
from werkzeug.middleware.proxy_fix import ProxyFix
from twilio.rest import Client
from datetime import datetime

# Load environment variables from .env
load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)  # Trust X-Forwarded-Proto for HTTPS detection

# Twilio and OpenAI config
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
validator = RequestValidator(TWILIO_AUTH_TOKEN)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# MySQL config
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

@app.route("/sms", methods=["POST"])
def sms_reply():
    twilio_signature = request.headers.get("X-Twilio-Signature", "")
    url = request.url
    post_vars = request.form.to_dict()

    if not validator.validate(url, post_vars, twilio_signature):
        print("❌ Invalid request signature — rejected.")
        return abort(403)

    from_number = post_vars.get("From")
    body = post_vars.get("Body", "").strip()
    print(f"📩 Message from {from_number}: {body}")

    db = mysql.connector.connect(**db_config)
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM deliveries
        WHERE phone_number = %s AND status IN ('awaiting_confirmation', 'correction_requested')
        ORDER BY created_at DESC
        LIMIT 1
    """, (from_number,))
    delivery = cursor.fetchone()

    if not delivery:
        twilio_client.messages.create(
            body="Sorry we are having problems with our text system right now. Please give us a call at 917-584-5634.",
            from_=twilio_number,
            to=from_number
        )
        return "No matching delivery found", 200

    cursor.execute("""
        INSERT INTO message_history (phone_number, role, message)
        VALUES (%s, 'user', %s)
    """, (from_number, body))
    db.commit()

    normalized_body = body.lower().strip()

    if normalized_body == "yes":
        cursor.execute("""
            UPDATE deliveries SET status = 'ready' WHERE id = %s
        """, (delivery["id"],))
        db.commit()
        reply = "Thanks! Your delivery has been confirmed."
        cursor.execute("""
            INSERT INTO message_history (phone_number, role, message)
            VALUES (%s, 'assistant', %s)
        """, (from_number, reply))
        db.commit()
        twilio_client.messages.create(body=reply, from_=twilio_number, to=from_number)
        return reply, 200

    if normalized_body == "no":
        cursor.execute("""
            UPDATE deliveries SET status = 'correction_requested' WHERE id = %s
        """, (delivery["id"],))
        db.commit()
        reply = "Got it. Is it the delivery address or time you’d like to change?"
        cursor.execute("""
            INSERT INTO message_history (phone_number, role, message)
            VALUES (%s, 'assistant', %s)
        """, (from_number, reply))
        db.commit()
        twilio_client.messages.create(body=reply, from_=twilio_number, to=from_number)
        return reply, 200

    cursor.execute("""
        SELECT role, message FROM message_history
        WHERE phone_number = %s
        ORDER BY created_at DESC
        LIMIT 5
    """, (from_number,))
    messages = cursor.fetchall()[::-1]

    context = [{"role": m["role"], "content": m["message"]} for m in messages]
    context.append({"role": "user", "content": body})

    try:
        # Step 1: Get assistant response
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": (
                    "You are a smart and friendly chatbot helping patients correct or confirm pharmacy deliveries.\n"
                    "Users may reply with vague phrases like 'address', 'zip code', 'time', etc.\n"
                    "Gently guide them to provide the missing information.\n"
                    "If the needed info is clear, respond naturally.\n"
                    "You must always guide toward confirming or correcting one of these: delivery address or delivery time.\n"
                    "Once a correction is received, do not ask the user if there's anything else to correct. Once a correction is received, confirm the update clearly and politely, and end the conversation. Never prompt for more input."
                )},
                *context
            ]
        )
        reply = response.choices[0].message.content.strip()
        cursor.execute("""
            INSERT INTO message_history (phone_number, role, message)
            VALUES (%s, 'assistant', %s)
        """, (from_number, reply))
        db.commit()
        twilio_client.messages.create(body=reply, from_=twilio_number, to=from_number)

        # Step 2: Try extracting corrections
        extract = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Return a JSON object with any updated delivery_address or delivery_time."},
                *context
            ],
            functions=[{
                "name": "extract_update",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "delivery_address": {"type": "string"},
                        "delivery_time": {"type": "string"}
                    }
                }
            }],
            function_call="auto"
        )

        if extract.choices[0].message.function_call:
            args = json.loads(extract.choices[0].message.function_call.arguments)
            updates = []
            values = []
            if "delivery_address" in args:
                updates.append("delivery_address = %s")
                values.append(args["delivery_address"])
            if "delivery_time" in args:
                updates.append("delivery_time = %s")
                values.append(args["delivery_time"])
            if updates:
                updates.append("correction_note = %s")
                values.append(f"{body} — fixed on {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                values.append(delivery["id"])
                cursor.execute(f"UPDATE deliveries SET {', '.join(updates)}, status = 'ready' WHERE id = %s", values)
                db.commit()

        return reply, 200

    except Exception as e:
        print("❌ GPT or DB error:", e)
        fallback = "Sorry, we had trouble understanding that. A human will follow up."
        twilio_client.messages.create(body=fallback, from_=twilio_number, to=from_number)
        return fallback, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
