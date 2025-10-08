from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    binance_client.FUTURES_URL = 'https://fapi.binance.com' 
except Exception as e:
    binance_client = None
    print(f"Could not initialize Binance Client. Error: {e}")

TELEGRAM_SYMBOL_MAP = {"BTCUSD": "BTCUSDC"}
BINANCE_SYMBOL_MAP = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT"}
TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def place_binance_order(signal, ticker, quantity):
    # This function for entry remains the same
    if not binance_client:
        return False, "Binance Client not initialized."
    try:
        trade_symbol = BINANCE_SYMBOL_MAP.get(ticker, ticker)
        trade_side = Client.SIDE_BUY if signal.upper() == 'LONG' else Client.SIDE_SELL
        
        print(f"Attempting to place FUTURES order: {trade_side} {quantity} of {trade_symbol}")
        order = binance_client.futures_create_order(
            symbol=trade_symbol,
            side=trade_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity)
        print("Binance Futures order successful:", order)
        return True, "Futures order placed successfully."
    except Exception as e:
        return False, f"Binance Futures API Error: {str(e)}"

# ===================================================================
# === NEW, CORRECTED FUNCTION FOR FUTURES EXITS ===
# ===================================================================
def close_futures_position(ticker):
    """Closes an open futures position by placing an opposing market order."""
    if not binance_client:
        return False, "Binance Client not initialized."

    try:
        trade_symbol = BINANCE_SYMBOL_MAP.get(ticker, ticker)
        
        # Get all open positions from the futures account
        positions = binance_client.futures_position_information()
        # Find the specific position for our symbol
        target_position = next((p for p in positions if p['symbol'] == trade_symbol), None)

        if target_position and float(target_position['positionAmt']) != 0:
            quantity = abs(float(target_position['positionAmt']))
            is_long = float(target_position['positionAmt']) > 0
            
            # To close a position, we place an order on the opposite side
            close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY
            
            print(f"Attempting to close FUTURES position: {close_side} {quantity} of {trade_symbol}")
            order = binance_client.futures_create_order(
                symbol=trade_symbol,
                side=close_side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=True # Important: ensures this order only closes a position
            )
            print("Binance Futures close order successful:", order)
            return True, f"Position for {trade_symbol} closed successfully."
        else:
            print(f"No open futures position found for {trade_symbol}.")
            return False, f"No open futures position found for {trade_symbol}."
            
    except BinanceAPIException as e:
        return False, f"Binance API Error on close: {e.message}"
    except Exception as e:
        return False, f"An unexpected error occurred on close: {str(e)}"
# ===================================================================
# === END OF NEW FUNCTION ===
# ===================================================================

@app.route('/')
def health_check():
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.data.decode('utf-8')
        print(f"Received message: {message}")

        parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
        action = parts.get('action', 'message').strip()
        original_ticker = parts.get('ticker', 'N/A').strip()
        
        if action == 'close':
            # This now calls the correct futures function
            success, result_message = close_futures_position(original_ticker)
            status_prefix = "✅" if success else "❌"
            formatted_message = (f"{status_prefix} **Position Close Signal**\n\n"
                                 f"**Ticker:** {original_ticker}\n"
                                 f"**Close Price:** ${parts.get('price', 'N/A').strip()}\n\n"
                                 f"**Binance Status:** {result_message}")
        else:
            # This part for entry/message remains the same
            binance_status_message = ""
            signal_type = parts.get('signal', 'N/A').upper().strip()
            final_ticker = TELEGRAM_SYMBOL_MAP.get(original_ticker, original_ticker)
            quantity = parts.get('qty', 'N/A').strip()
            
            if action == 'enter':
                # This needs to call the futures entry function
                success, result_message = place_binance_order(signal_type, original_ticker, quantity)
                binance_status_message = f"✅ **Binance Order:** {result_message}" if success else f"❌ **Binance Order:** {result_message}"
            else:
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

