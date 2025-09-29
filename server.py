from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
# These are read from Render's Environment Variables for security
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# Initialize the Binance Client safely
try:
    if BINANCE_API_KEY and BINANCE_API_SECRET:
        binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    else:
        binance_client = None
        print("Binance API keys not found. Auto-trading features will be disabled.")
except Exception as e:
    binance_client = None
    print(f"Could not initialize Binance Client. Error: {e}")

# --- SYMBOL MAPPING CONFIGURATION ---
TELEGRAM_SYMBOL_MAP = {"BTCUSD": "BTCUSDC"}
BINANCE_SYMBOL_MAP = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def place_binance_order(signal, ticker, quantity):
    """Places a market order on Binance for trade entry."""
    if not binance_client:
        return False, "Binance Client not initialized. Check API keys."
    try:
        trade_symbol = BINANCE_SYMBOL_MAP.get(ticker, ticker)
        trade_side = Client.SIDE_BUY if signal.upper() == 'LONG' else Client.SIDE_SELL
        
        print(f"Attempting to place order: {trade_side} {quantity} of {trade_symbol}")
        order = binance_client.create_order(
            symbol=trade_symbol,
            side=trade_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity)
        print("Binance order successful:", order)
        return True, "Order placed successfully."
    except BinanceAPIException as e:
        error_message = f"Binance API Error: {e.message}"
        print(error_message)
        return False, error_message
    except Exception as e:
        error_message = f"An unexpected error occurred: {str(e)}"
        print(error_message)
        return False, error_message

def close_binance_position(ticker):
    """Closes all open positions for a given spot asset by selling it."""
    if not binance_client:
        return False, "Binance Client not initialized."

    try:
        trade_symbol = BINANCE_SYMBOL_MAP.get(ticker, ticker)
        asset = trade_symbol.replace('USDT', '') # e.g., 'BTC' from 'BTCUSDT'
        
        # Get the current balance for the asset
        balance = binance_client.get_asset_balance(asset=asset)
        if balance and float(balance['free']) > 0:
            quantity_to_sell = float(balance['free'])
            
            print(f"Attempting to close position: SELL {quantity_to_sell} of {trade_symbol}")
            order = binance_client.create_order(
                symbol=trade_symbol,
                side=Client.SIDE_SELL,
                type=Client.ORDER_TYPE_MARKET,
                quantity=quantity_to_sell
            )
            print("Binance close order successful:", order)
            return True, f"Position for {trade_symbol} closed successfully."
        else:
            print(f"No open position found for asset {asset}.")
            return False, f"No open position found for {asset}."
            
    except BinanceAPIException as e:
        error_message = f"Binance API Error on close: {e.message}"
        print(error_message)
        return False, error_message
    except Exception as e:
        error_message = f"An unexpected error occurred on close: {str(e)}"
        print(error_message)
        return False, error_message

@app.route('/')
def health_check():
    """Health check endpoint for UptimeRobot."""
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    """Main webhook to receive and process alerts from TradingView."""
    try:
        message = request.data.decode('utf-8')
        print(f"Received message: {message}")

        parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
        action = parts.get('action', 'message').strip()
        original_ticker = parts.get('ticker', 'N/A').strip()
        
        # --- LOGIC TO HANDLE DIFFERENT ACTIONS ---
        if action == 'close':
            success, result_message = close_binance_position(original_ticker)
            status_prefix = "✅" if success else "❌"
            formatted_message = (f"{status_prefix} **Position Close Signal**\n\n"
                                 f"**Ticker:** {original_ticker}\n"
                                 f"**Close Price:** ${parts.get('price', 'N/A').strip()}\n\n"
                                 f"**Binance Status:** {result_message}")
        else: # Handles both 'enter' and 'message'
            binance_status_message = ""
            signal_type = parts.get('signal', 'N/A').upper().strip()
            final_ticker = TELEGRAM_SYMBOL_MAP.get(original_ticker, original_ticker)
            quantity = parts.get('qty', 'N/A').strip()
            
            if action == 'enter':
                success, result_message = place_binance_order(signal_type, original_ticker, quantity)
                binance_status_message = f"✅ **Binance Order:** {result_message}" if success else f"❌ **Binance Order:** {result_message}"
            else: # action == 'message'
                binance_status_message = "*(Notification Only)*"

            price = parts.get('price', 'N/A').strip()
            stop_loss = parts.get('sl', 'N/A').strip()
            take_profit = parts.get('tp', 'N/A').strip()
            
            formatted_message = (
                f"🚨 New TradingView Alert! {binance_status_message} 🚨\n\n"
                f"**Signal:** {signal_type}\n"
                f"**Ticker:** {final_ticker}\n\n"
                f"**Entry Price:** ${price}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Stop Loss:** ${stop_loss}\n"
                f"**Take Profit:** ${take_profit}"
            )

        # Send the final message to Telegram
        payload = {"chat_id": CHAT_ID, "text": formatted_message, "parse_mode": "Markdown"}
        r = requests.post(TELEGRAM_URL, json=payload)
        r.raise_for_status()
        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"FATAL ERROR in webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

