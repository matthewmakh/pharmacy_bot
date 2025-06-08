📦 Pharmacy Delivery Confirmation Bot

A Twilio + OpenAI-powered Flask app that confirms pharmacy deliveries via SMS. Users can confirm or correct delivery details conversationally — and the bot intelligently updates only what’s necessary (like the delivery address or time).

---

🚀 Features

- 🧠 GPT-4 powered: Handles vague replies like “zip code” or “address” and guides users to clarify.
- ✅ Auto-updates delivery info: Only updates the corrected fields (address or time).
- 💬 Natural replies: The bot responds in a friendly, human-like way.
- 🔒 Safe logic: No unnecessary open-ended questions like “Is there anything else?”
- 🗃 Message logging: Stores both user and bot messages to improve context over time.
- 📊 Streamlit admin panel: Add, view, and manage delivery records via a clean UI.

---

🧩 Stack

- **Backend:** Flask
- **Messaging:** Twilio
- **AI Responses:** OpenAI GPT-4
- **Database:** MySQL
- **Admin UI:** Streamlit
