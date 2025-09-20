from flask import Flask, request, jsonify
import requests
import os # Import the 'os' module to read environment variables

app = Flask(__name__)

# --- CRITICAL CHANGES FOR RENDER ---
# The script will now get these secrets from Render's Environment Variables section.
# This is much more secure than hard-coding them in the file.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
# ------------------------------------

# Check if the secrets were loaded correctly
if not BOT_TOKEN or not CHAT_ID:
    print("FATAL ERROR: BOT_TOKEN or CHAT_ID environment variables are not set.")
    # In a real app, you might want to exit here, but for Render,
    # it will just log the error and fail on requests.

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

@app.route('/webhook', methods=['POST'])
def webhook():
    # This function remains the same. It handles the incoming alert.
    try:
        message = request.data.decode('utf-8')
        print(f"Received message: {message}")

        try:
            # Safely parse the incoming message
            parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
            
            signal_type = parts.get('signal', 'N/A').upper()
            ticker = parts.get('ticker', 'N/A')
            price = parts.get('price', 'N/A')
            quantity = parts.get('qty', 'N/A')
            stop_loss = parts.get('sl', 'N/A')
            take_profit = parts.get('tp', 'N/A')
            
            # Build the formatted message for Telegram
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
            # If parsing fails, send the raw message so you don't lose the alert
            formatted_message = f"Received unformatted alert:\n\n{message}"

        # Prepare the payload to send to Telegram
        payload = {
            "chat_id": CHAT_ID, 
            "text": formatted_message,
            "parse_mode": "Markdown"
        }
        
        r = requests.post(TELEGRAM_URL, json=payload)
        r.raise_for_status() # This will raise an error if the request fails (e.g., bad token)

        print(f"Telegram API Response: {r.json()}")
        return jsonify({"status": "success", "sent_message": formatted_message}), 200

    except Exception as e:
        error_message = f"An error occurred in the webhook function: {str(e)}"
        print(error_message)
        return jsonify({"status": "error", "message": error_message}), 500

if __name__ == '__main__':
    # --- CRITICAL CHANGE FOR RENDER ---
    # Render tells our app which port to listen on through the PORT environment variable.
    # The default is 5000 for local testing.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
