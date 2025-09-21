from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("FATAL ERROR: BOT_TOKEN or CHAT_ID environment variables are not set.")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# --- NEW CODE ADDED FOR UPTIMEROBOT ---
@app.route('/')
def health_check():
    """
    This is a health check endpoint for UptimeRobot.
    It responds with a simple success message so UptimeRobot knows the service is live.
    """
    return "OK", 200
# ----------------------------------------

@app.route('/webhook', methods=['POST'])
def webhook():
    # ... (the rest of your webhook code is unchanged) ...
    try:
        message = request.data.decode('utf-8')
        print(f"Received message: {message}")

        try:
            parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
            
            signal_type = parts.get('signal', 'N/A').upper()
            ticker = parts.get('ticker', 'N/A')
            price = parts.get('price', 'N/A')
            quantity = parts.get('qty', 'N/A')
            stop_loss = parts.get('sl', 'N/A')
            take_profit = parts.get('tp', 'N/A')
            
            formatted_message = (
                f"🚨 New TradingView Alert! 🚨\n\n"
                f"Signal: **{signal_type}**\n"
                f"Ticker: {ticker}\n\n"
                f"Entry Price: ${price}\n"
                f"Quantity: {quantity}\n\n"
                f"Stop Loss: ${stop_loss}\n"
                f"Take Profit: ${take_profit}"
            )
        except Exception as e:
            print(f"Error parsing message body: {e}")
            formatted_message = f"Received unformatted alert:\n\n{message}"

        payload = {
            "chat_id": CHAT_ID, 
            "text": formatted_message,
            "parse_mode": "Markdown"
        }
        
        r = requests.post(TELEGRAM_URL, json=payload)
        r.raise_for_status()

        print(f"Telegram API Response: {r.json()}")
        return jsonify({"status": "success", "sent_message": formatted_message}), 200

    except Exception as e:
        error_message = f"An error occurred in the webhook function: {str(e)}"
        print(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

