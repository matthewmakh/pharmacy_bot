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

# 🌱 Load environment variables
load_dotenv()

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

# 🔐 Twilio + OpenAI setup
twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")
validator = RequestValidator(os.getenv("TWILIO_AUTH_TOKEN"))
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🛠 MySQL config
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

@app.route("/sms", methods=["POST"])
def sms_reply():
    # 🧾 Twilio request validation
    twilio_signature = request.headers.get("X-Twilio-Signature", "")
    url = request.url
    post_vars = request.form.to_dict()
    if not validator.validate(url, post_vars, twilio_signature):
        return abort(403)

    from_number = post_vars.get("From")
    body = post_vars.get("Body", "").strip()
    print(f"📩 Message from {from_number}: {body}")

    db = mysql.connector.connect(**db_config)
    cursor = db.cursor(dictionary=True)

    # 📦 Get latest delivery for this number
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

    # 💾 Log user message
    cursor.execute("""
        INSERT INTO message_history (phone_number, role, message)
        VALUES (%s, 'user', %s)
    """, (from_number, body))
    db.commit()

    # 🧠 Analyze recent message history
    cursor.execute("""
        SELECT role, message FROM message_history
        WHERE phone_number = %s
        ORDER BY created_at DESC
        LIMIT 5
    """, (from_number,))
    messages = cursor.fetchall()[::-1]
    context = [{"role": m["role"], "content": m["message"]} for m in messages]
    context.append({"role": "user", "content": body})
    normalized = body.lower().strip()

    # ⚡ Step 1: Detect if message is a confirmation or correction
    if normalized in ("yes", "no"):
        try:
            intent_check = openai_client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": (
                        "Determine if the user message is meant to confirm delivery info (intent=confirm), "
                        "correct it (intent=correction), or is unclear/confused (intent=other). "
                        "Return just the intent as one of: confirm, correction, other."
                    )},
                    *context
                ],
                temperature=0.2
            )
            intent = intent_check.choices[0].message.content.strip().lower()

            if normalized == "yes" and intent == "confirm":
                cursor.execute("UPDATE deliveries SET status = 'ready' WHERE id = %s", (delivery["id"],))
                db.commit()
                reply = "Thanks! Your delivery has been confirmed."
                cursor.execute("""
                    INSERT INTO message_history (phone_number, role, message)
                    VALUES (%s, 'assistant', %s)
                """, (from_number, reply))
                db.commit()
                twilio_client.messages.create(body=reply, from_=twilio_number, to=from_number)
                return reply, 200

            elif normalized == "no" and intent == "correction":
                cursor.execute("UPDATE deliveries SET status = 'correction_requested' WHERE id = %s", (delivery["id"],))
                db.commit()
                reply = "Got it. Is it the delivery address or time you’d like to change?"
                cursor.execute("""
                    INSERT INTO message_history (phone_number, role, message)
                    VALUES (%s, 'assistant', %s)
                """, (from_number, reply))
                db.commit()
                twilio_client.messages.create(body=reply, from_=twilio_number, to=from_number)
                return reply, 200

            # 🤔 fallback: continue to GPT chat flow if unclear
        except Exception as e:
            print("⚠️ Intent detection failed:", e)

    # 🤖 Step 2: Get assistant response (chat)
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": (
                    "You are a smart and friendly chatbot helping patients correct or confirm pharmacy deliveries.\n"
                    "Users may reply with vague phrases like 'zip code' or 'time'.\n"
                    "Always guide toward correcting or confirming delivery address or time.\n"
                    "Do not ask if there's anything else. Always end clearly after one fix."
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

        # 🧩 Step 3: Try structured correction extraction
        extract = openai_client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Return a JSON object with updated delivery_address or delivery_time."},
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
            updates, values = [], []
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
                cursor.execute(f"""
                    UPDATE deliveries SET {', '.join(updates)}, status = 'ready'
                    WHERE id = %s
                """, values)
                db.commit()

        return reply, 200

    except Exception as e:
        print("❌ GPT or DB error:", e)
        fallback = "Sorry, we had trouble understanding that. A human will follow up."
        twilio_client.messages.create(body=fallback, from_=twilio_number, to=from_number)
        return fallback, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
