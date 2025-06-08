from flask import Flask, request, abort
from openai import OpenAI
import mysql.connector
from dotenv import load_dotenv
from twilio.request_validator import RequestValidator
import os
import json
from werkzeug.middleware.proxy_fix import ProxyFix
from twilio.rest import Client

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
    # 🔒 Validate Twilio request
    twilio_signature = request.headers.get("X-Twilio-Signature", "")
    url = request.url
    post_vars = request.form.to_dict()

    if not validator.validate(url, post_vars, twilio_signature):
        print("❌ Invalid request signature — rejected.")
        return abort(403)

    # ✅ Extract incoming message
    from_number = post_vars.get("From")
    body = post_vars.get("Body", "").strip().lower()
    print(f"📩 Message from {from_number}: {body}")

    # Connect to MySQL
    db = mysql.connector.connect(**db_config)
    cursor = db.cursor(dictionary=True)

    # Find latest delivery for this number
    cursor.execute("""
        SELECT * FROM deliveries
        WHERE phone_number = %s AND status IN ('awaiting_confirmation', 'correction_requested')
        ORDER BY created_at DESC
        LIMIT 1
    """, (from_number,))
    delivery = cursor.fetchone()

    if not delivery:
        twilio_client.messages.create(
            body="We couldn’t find any delivery associated with your number. Please wait for a new message or call us.",
            from_=twilio_number,
            to=from_number
        )
        return "No matching delivery found", 200

    # 🔍 Step 1: Let GPT classify the intent
    print("🤖 Analyzing intent with GPT...")

    intent_prompt = f"""
A pharmacy sent a patient their delivery info. The patient replied:

"{body}"

Classify this message strictly as one of the following single words:
- "confirm" (if everything looks good)
- "correction" (if they are requesting a change)
- "unclear" (if it's ambiguous or cannot be determined)

Only return one of those three words — no explanation.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": intent_prompt}],
            temperature=0
        )
        intent = response.choices[0].message.content.strip().lower()
        print(f"🔍 GPT classified intent as: {intent}")
    except Exception as e:
        print("❌ GPT classification failed:", e)
        return "Sorry, we couldn't understand your message.", 200

    # 🔀 Step 2: Handle logic based on GPT intent
    if intent == "confirm":
        cursor.execute("""
            UPDATE deliveries
            SET status = 'ready'
            WHERE id = %s
        """, (delivery['id'],))
        db.commit()
        print("✅ Marked as ready via GPT intent.")
        twilio_client.messages.create(
            body="Thanks! We’ve marked it as ready.",
            from_=twilio_number,
            to=from_number
        )
        return "Thanks! We’ve marked it as ready.", 200

    elif intent == "correction":
        cursor.execute("""
            UPDATE deliveries
            SET status = 'correction_requested'
            WHERE id = %s
        """, (delivery['id'],))
        db.commit()
        print("⚠️ Marked as correction_requested via GPT intent.")
        twilio_client.messages.create(
            body="Got it. What needs to be corrected?",
            from_=twilio_number,
            to=from_number
        )
        return "Got it. What needs to be corrected?", 200

    else:
        print("🤖 Intent was unclear — sending full prompt for correction extraction...")

        correction_prompt = f"""
You're an AI assistant helping a pharmacy confirm deliveries. Here's the original delivery info:

Name: {delivery['patient_name']}
Address: {delivery['delivery_address']}
Time: {delivery['delivery_time']}

The patient replied: "{body}"

Determine if the message includes a correction to the address or time.
If it does, return a JSON object like:
{{
    "delivery_address": "new address if corrected, else null",
    "delivery_time": "new time if corrected, else null"
}}

If there's nothing to change, return:
{{
    "delivery_address": null,
    "delivery_time": null
}}
"""

        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": correction_prompt}],
                temperature=0.3
            )
            reply = response.choices[0].message.content.strip()
            print("🧠 GPT response:", reply)

            correction = json.loads(reply)
            updates = []
            values = []

            if correction["delivery_address"]:
                updates.append("delivery_address = %s")
                values.append(correction["delivery_address"])

            if correction["delivery_time"]:
                updates.append("delivery_time = %s")
                values.append(correction["delivery_time"])

            if updates:
                updates.append("correction_note = %s")
                values.append(body)
                values.append(delivery['id'])

                update_query = f"""
                    UPDATE deliveries
                    SET {', '.join(updates)},
                        status = 'ready'
                    WHERE id = %s
                """
                cursor.execute(update_query, values)
                db.commit()
                print("✅ Updated delivery with GPT corrections.")
                twilio_client.messages.create(
                    body="Thanks! We’ve updated your delivery as requested.",
                    from_=twilio_number,
                    to=from_number
                )
                return "Thanks! We’ve updated your delivery as requested.", 200
            else:
                twilio_client.messages.create(
                    body="Thanks! No changes were needed.",
                    from_=twilio_number,
                    to=from_number
                )
                return "Thanks! No changes were needed.", 200

        except Exception as e:
            print("❌ GPT correction logic failed:", e)
            twilio_client.messages.create(
                body="Sorry, we had trouble understanding that. A human will review it.",
                from_=twilio_number,
                to=from_number
            )
            return "Sorry, we had trouble understanding that. A human will review it.", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
