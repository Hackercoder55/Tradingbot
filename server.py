from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
# These are read from Render's Environment Variables for security
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# Initialize the Binance Client
# This will fail gracefully if keys are missing, and the error will be caught later
try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
except Exception as e:
    binance_client = None
    print(f"Could not initialize Binance Client. Check API keys. Error: {e}")


# --- SYMBOL MAPPING CONFIGURATION ---
TELEGRAM_SYMBOL_MAP = {"BTCUSD": "BTCUSDC"}
BINANCE_SYMBOL_MAP = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
# ------------------------------------

if not all([BOT_TOKEN, CHAT_ID]):
    print("FATAL ERROR: BOT_TOKEN or CHAT_ID environment variables are not set.")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def place_binance_order(signal, ticker, quantity):
    """
    Places a market order on Binance.
    """
    if not binance_client:
        return False, "Binance Client not initialized. Check API keys."

    try:
        trade_symbol = BINANCE_SYMBOL_MAP.get(ticker, ticker)
        trade_side = Client.SIDE_BUY if signal == 'LONG' else Client.SIDE_SELL
        
        print(f"Attempting to place order: {trade_side} {quantity} of {trade_symbol}")
        
        # Place a market order
        order = binance_client.create_order(
            symbol=trade_symbol,
            side=trade_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity
        )
        print("Binance order successful:", order)
        return True, "Order placed successfully."
        
    except Exception as e:
        error_message = f"Binance API Error: {str(e)}"
        print(error_message)
        return False, error_message

@app.route('/')
def health_check():
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.data.decode('utf-8')
        print(f"Received message: {message}")

        binance_status_message = ""
        try:
            parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
            
            # --- NEW CONDITIONAL LOGIC ---
            action = parts.get('action', 'enter') # Default to 'enter' for older scripts
            
            signal_type = parts.get('signal', 'N/A').upper()
            original_ticker = parts.get('ticker', 'N/A')
            final_ticker = TELEGRAM_SYMBOL_MAP.get(original_ticker, original_ticker)
            quantity = parts.get('qty', 'N/A')
            
            # Only execute a trade if the action from Pine Script is 'enter'
            if action == 'enter':
                success, result_message = place_binance_order(signal_type, original_ticker, quantity)
                binance_status_message = f"✅ **Binance Order:** {result_message}" if success else f"❌ **Binance Order:** {result_message}"
            else:
                # If the action is 'message', set a notification-only status
                binance_status_message = "*(Notification Only)*"
            # --------------------------------

            price = parts.get('price', 'N/A')
            stop_loss = parts.get('sl', 'N/A')
            take_profit = parts.get('tp', 'N/A')
            
            # Updated message to include the dynamic status
            formatted_message = (
                f"🚨 New TradingView Alert! {binance_status_message} 🚨\n\n"
                f"**Signal:** {signal_type}\n"
                f"**Ticker:** {final_ticker}\n\n"
                f"**Entry Price:** ${price}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Stop Loss:** ${stop_loss}\n"
                f"**Take Profit:** ${take_profit}"
            )
        except Exception as e:
            print(f"Error parsing message body: {e}")
            formatted_message = f"Received unformatted alert:\n\n{message}"

        payload = { "chat_id": CHAT_ID, "text": formatted_message, "parse_mode": "Markdown" }
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

