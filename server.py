from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
import json # Import the json library

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# --- STRATEGY CONFIGURATION ---
TRADE_SYMBOL = "BTCUSDC"  # The symbol we are trading on Binance
LEVERAGE = 125
FIXED_STOP_LOSS_POINTS = 200  # 200 points ($)
FIXED_TAKE_PROFIT_POINTS = 1300 # 1300 points ($) (Derived from 6.5 RR * 200 SL)

# --- INITIALIZE BINANCE CLIENT ---
try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    # Ensure connection is to Futures Testnet or Mainnet as appropriate
    # For Mainnet:
    binance_client.FUTURES_URL = 'https://fapi.binance.com'
    # For Testnet (if you want to test without real money):
    # binance_client.FUTURES_URL = 'https://testnet.binancefuture.com'
    # binance_client.API_URL = 'https://testnet.binancefuture.com/fapi' # Needed for testnet client setup

    server_time = binance_client.futures_time()
    logging.info(f"Successfully connected to Binance Futures. Server time: {server_time['serverTime']}")
except Exception as e:
    binance_client = None
    logging.error(f"FATAL: Could not initialize Binance Client. Error: {e}")

# --- HELPER FUNCTIONS ---
def send_telegram_message(message):
    if not BOT_TOKEN or not CHAT_ID:
        logging.warning("Telegram BOT_TOKEN or CHAT_ID not set.")
        return
    try:
        TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        response = requests.post(TELEGRAM_URL, json=payload)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")

def set_leverage(symbol, leverage):
    if not binance_client: return False, "Binance client not initialized."
    try:
        response = binance_client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage change response for {symbol}: {response}")
        return True, f"Leverage set to {leverage}x (or already was)."
    except BinanceAPIException as e:
        if e.code == -4046: # "No need to change leverage"
            logging.info(f"Leverage for {symbol} is already {leverage}x.")
            return True, f"Leverage is already {leverage}x."
        logging.error(f"Error setting leverage for {symbol}: Code={e.code}, Msg={e.message}")
        return False, f"Failed to set leverage: {e.message}"
    except Exception as e:
        logging.error(f"Unexpected error setting leverage for {symbol}: {e}")
        return False, f"Unexpected error setting leverage: {str(e)}"

def place_entry_order(signal, quantity):
    if not binance_client: return None, "Binance Client not initialized."
    try:
        trade_side = Client.SIDE_BUY if signal.upper() == 'BUY' else Client.SIDE_SELL
        logging.info(f"Attempting to place FUTURES entry order: {trade_side} {quantity} of {TRADE_SYMBOL}")
        order = binance_client.futures_create_order(
            symbol=TRADE_SYMBOL, side=trade_side, type=Client.ORDER_TYPE_MARKET, quantity=quantity)
        logging.info(f"Binance Futures entry successful: {order}")
        return order, "Futures entry order placed successfully."
    except BinanceAPIException as e:
        logging.error(f"Binance Futures API Error on entry: Code={e.code}, Msg={e.message}")
        return None, f"Binance Futures API Error: {e.message}"
    except Exception as e:
        logging.error(f"Unexpected error placing entry order: {str(e)}")
        return None, f"Unexpected error placing entry order: {str(e)}"

def place_sl_tp_orders(side, entry_price):
    if not binance_client: return "Binance Client not initialized."
    is_long = side.upper() == Client.SIDE_BUY
    stop_loss_price = round(entry_price - FIXED_STOP_LOSS_POINTS if is_long else entry_price + FIXED_STOP_LOSS_POINTS, 2)
    take_profit_price = round(entry_price + FIXED_TAKE_PROFIT_POINTS if is_long else entry_price - FIXED_TAKE_PROFIT_POINTS, 2)
    close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY
    sl_tp_status = ""
    # Cancel existing SL/TP orders for this symbol first (safety measure)
    try:
        logging.info(f"Attempting to cancel existing SL/TP orders for {TRADE_SYMBOL} before placing new ones.")
        open_orders = binance_client.futures_get_open_orders(symbol=TRADE_SYMBOL)
        for order in open_orders:
            if order['type'] in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                binance_client.futures_cancel_order(symbol=TRADE_SYMBOL, orderId=order['orderId'])
                logging.info(f"Cancelled existing order ID: {order['orderId']}")
    except Exception as e:
        logging.warning(f"Could not cancel existing orders (might be none): {e}")

    # Place new SL/TP orders
    try:
        logging.info(f"Placing STOP_MARKET order at {stop_loss_price}")
        binance_client.futures_create_order(
            symbol=TRADE_SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=stop_loss_price, reduceOnly=True, closePosition=True, timeInForce='GTC') # Use GTC for stop orders
        sl_tp_status += f"✅ Stop-Loss set at ${stop_loss_price}\n"
    except BinanceAPIException as e:
        logging.error(f"Error placing Stop-Loss order: Code={e.code}, Msg={e.message}")
        sl_tp_status += f"❌ Failed to set Stop-Loss: {e.message}\n"
    except Exception as e:
        logging.error(f"Unexpected error placing Stop-Loss order: {e}")
        sl_tp_status += f"❌ Unexpected error setting Stop-Loss: {str(e)}\n"

    try:
        logging.info(f"Placing TAKE_PROFIT_MARKET order at {take_profit_price}")
        binance_client.futures_create_order(
            symbol=TRADE_SYMBOL, side=close_side, type='TAKE_PROFIT_MARKET', stopPrice=take_profit_price, reduceOnly=True, closePosition=True, timeInForce='GTC') # Use GTC for TP orders
        sl_tp_status += f"✅ Take-Profit set at ${take_profit_price}"
    except BinanceAPIException as e:
        logging.error(f"Error placing Take-Profit order: Code={e.code}, Msg={e.message}")
        sl_tp_status += f"❌ Failed to set Take-Profit: {e.message}"
    except Exception as e:
        logging.error(f"Unexpected error placing Take-Profit order: {e}")
        sl_tp_status += f"❌ Unexpected error setting Take-Profit: {str(e)}"
    return sl_tp_status

# --- FLASK ROUTES ---
@app.route('/')
def health_check():
    # You can access http://your_ip/ to check if the server is running
    return "Bot server is running.", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # --- PARSE JSON DATA ---
        try:
            data = request.get_json()
            if not data or not isinstance(data, dict):
                 message_str = request.data.decode('utf-8')
                 logging.warning(f"Received non-JSON or invalid data: {message_str}")
                 return jsonify({"status": "error", "message": "Expected valid JSON data"}), 400
            logging.info(f"Received webhook JSON data: {data}")
        except Exception as parse_error:
            logging.error(f"Could not parse request JSON data: {parse_error}")
            return jsonify({"status": "error", "message": "Could not parse JSON request data"}), 400

        # --- Extract action ('BUY' or 'SELL') ---
        signal_type = data.get('action', '').upper().strip()
        if signal_type not in ['BUY', 'SELL']:
            logging.warning(f"Ignoring message: Invalid or missing 'action'. Received: {signal_type}")
            # Do not send Telegram message for ignored alerts unless debugging
            return jsonify({"status": "ignored, invalid action"}), 200 # Return 200 OK so TradingView doesn't retry

        # --- Extract quantity ---
        try:
            quantity_str = data.get('qty')
            if quantity_str is None:
                raise ValueError("Quantity ('qty') missing from JSON payload.")
            quantity = float(quantity_str)
            if quantity <= 0:
                raise ValueError("Quantity must be positive.")
        except (ValueError, TypeError) as qty_error:
            logging.error(f"Could not parse valid quantity from data: {data.get('qty')}. Error: {qty_error}")
            send_telegram_message(f"❌ **Trade Failed!**\nCould not parse valid quantity from alert: `{data.get('qty')}`")
            return jsonify({"status": "error", "message": f"Invalid quantity: {qty_error}"}), 400

        # --- FILTERS REMOVED ---
        # No more quantity checks here

        # --- EXECUTE THE TRADE ---
        # 1. Set Leverage (important before placing order)
        leverage_success, leverage_message = set_leverage(TRADE_SYMBOL, LEVERAGE)
        if not leverage_success:
            # Send failure message if leverage setting fails critically
            send_telegram_message(f"❌ **Trade Failed!**\nCould not set leverage.\n**Binance Error:** {leverage_message}")
            return jsonify({"status": "error", "message": "Failed to set leverage"}), 500

        # 2. Place Entry Order
        entry_order, entry_message = place_entry_order(signal_type, quantity)

        if entry_order and entry_order.get('avgPrice'): # Check if order was placed and filled
            # 3. If Entry is successful, get entry price and place SL/TP
            entry_price = float(entry_order['avgPrice'])
            order_side = entry_order['side'] # Use the actual side from the filled order confirmation
            sl_tp_message = place_sl_tp_orders(order_side, entry_price)

            # 4. Send success message to Telegram
            final_tg_message = (
                f"✅ **New Automated Trade Placed!** ✅\n\n"
                f"**Signal:** {signal_type}\n" # Will show BUY or SELL
                f"**Ticker:** {TRADE_SYMBOL}\n\n"
                f"**Entry Price:** ${entry_price}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Binance Status:**\n{sl_tp_message}"
            )
            status_code = 200
        else:
            # 4. Send failure message to Telegram
            final_tg_message = (
                f"❌ **Trade Failed!** ❌\n\n"
                f"**Signal:** {signal_type}\n"
                f"**Ticker:** {TRADE_SYMBOL}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Binance Error:** {entry_message or 'Order placement failed or did not return avgPrice.'}"
            )
            status_code = 500 # Indicate server error if trade placement failed

        send_telegram_message(final_tg_message)
        return jsonify({"status": "processed", "binance_message": entry_message if not entry_order else "Trade successful"}), status_code

    except Exception as e:
        logging.exception(f"FATAL ERROR in webhook processing: {e}") # Log the full traceback
        send_telegram_message(f"🚨 **FATAL BOT ERROR** 🚨\n\nThe server encountered a critical error. Check logs.")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- WSGI Entry Point (for Gunicorn) ---
# This part remains the same, but we need the wsgi.py file

if __name__ == '__main__':
    # This block is mainly for local testing, Gunicorn uses the 'app' object via wsgi.py
    port = int(os.environ.get("PORT", 5000))
    # Use waitress for a production-ready server if running directly (better than Flask dev server)
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        logging.warning("Waitress not found, using Flask development server (NOT FOR PRODUCTION).")
        app.run(host="0.0.0.0", port=port)
