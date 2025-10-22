from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
import json
from dotenv import load_dotenv # <-- NEW IMPORT

load_dotenv() # <-- NEW: Explicitly load .env file variables

# --- Basic Logging Setup ---
# (Keep your existing logging setup)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
# These will now be loaded reliably by load_dotenv() from your .env file
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# --- STRATEGY CONFIGURATION ---
TRADE_SYMBOL = "BTCUSDC"
LEVERAGE = 125
FIXED_STOP_LOSS_POINTS = 200
FIXED_TAKE_PROFIT_POINTS = 1300

# --- INITIALIZE BINANCE CLIENT ---
# This section remains critical. If it fails now, the keys ARE wrong or permissions are off.
try:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise ValueError("Binance API Key or Secret not found in environment variables.")
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    binance_client.FUTURES_URL = 'https://fapi.binance.com'
    server_time = binance_client.futures_time()
    logging.info(f"Successfully connected to Binance Futures. Server time: {server_time['serverTime']}")
except Exception as e:
    binance_client = None
    # Log the specific error during initialization VERY CLEARLY
    logging.error(f"FATAL: Could not initialize Binance Client during startup. Error: {e}")
    # Optionally send a Telegram alert that the bot failed to start
    # send_telegram_message("🚨 BOT STARTUP FAILED: Could not connect to Binance. Check API keys and server logs.")


# --- HELPER FUNCTIONS (send_telegram_message, set_leverage, place_entry_order, place_sl_tp_orders remain the same) ---
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
    if not binance_client: return False, "Binance client not initialized." # This error points to startup failure
    try:
        response = binance_client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage change response for {symbol}: {response}")
        return True, f"Leverage set to {leverage}x (or already was)."
    except BinanceAPIException as e:
        if e.code == -4046:
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
    try:
        logging.info(f"Attempting to cancel existing SL/TP orders for {TRADE_SYMBOL}")
        open_orders = binance_client.futures_get_open_orders(symbol=TRADE_SYMBOL)
        for order in open_orders:
            if order['type'] in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']:
                binance_client.futures_cancel_order(symbol=TRADE_SYMBOL, orderId=order['orderId'])
                logging.info(f"Cancelled existing order ID: {order['orderId']}")
    except Exception as e:
        logging.warning(f"Could not cancel existing orders (might be none): {e}")
    try:
        logging.info(f"Placing STOP_MARKET order at {stop_loss_price}")
        binance_client.futures_create_order(
            symbol=TRADE_SYMBOL, side=close_side, type='STOP_MARKET', stopPrice=stop_loss_price, reduceOnly=True, closePosition=True, timeInForce='GTC')
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
            symbol=TRADE_SYMBOL, side=close_side, type='TAKE_PROFIT_MARKET', stopPrice=take_profit_price, reduceOnly=True, closePosition=True, timeInForce='GTC')
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
    return "Bot server is running.", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global binance_client # Allow modifying the global client if re-initialization is needed
    try:
        # Check if client failed during startup
        if binance_client is None:
            logging.error("Webhook received but Binance client is not initialized (failed on startup).")
            # Try to re-initialize - maybe temporary issue?
            try:
                logging.info("Attempting to re-initialize Binance client...")
                if not BINANCE_API_KEY or not BINANCE_API_SECRET:
                     raise ValueError("Binance API Key or Secret still missing.")
                binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
                binance_client.FUTURES_URL = 'https://fapi.binance.com'
                server_time = binance_client.futures_time()
                logging.info(f"Re-initialization successful. Server time: {server_time['serverTime']}")
                send_telegram_message("✅ Bot recovered connection to Binance.")
            except Exception as reinit_e:
                logging.error(f"Re-initialization failed: {reinit_e}")
                send_telegram_message(f"🚨 BOT ERROR: Still unable to connect to Binance. Check keys/logs.")
                return jsonify({"status": "error", "message": "Binance client failed to initialize"}), 500

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
            return jsonify({"status": "ignored, invalid action"}), 200

        # --- Extract quantity ---
        try:
            quantity_str = data.get('qty')
            if quantity_str is None: raise ValueError("'qty' missing.")
            quantity = float(quantity_str)
            if quantity <= 0: raise ValueError("Quantity must be positive.")
        except (ValueError, TypeError) as qty_error:
            logging.error(f"Invalid quantity: {data.get('qty')}. Error: {qty_error}")
            send_telegram_message(f"❌ **Trade Failed!**\nInvalid quantity: `{data.get('qty')}`")
            return jsonify({"status": "error", "message": f"Invalid quantity: {qty_error}"}), 400

        # --- EXECUTE THE TRADE ---
        leverage_success, leverage_message = set_leverage(TRADE_SYMBOL, LEVERAGE)
        if not leverage_success:
            send_telegram_message(f"❌ **Trade Failed!**\nLeverage Error.\n**Binance:** {leverage_message}")
            return jsonify({"status": "error", "message": "Failed leverage"}), 500

        entry_order, entry_message = place_entry_order(signal_type, quantity)

        if entry_order and entry_order.get('avgPrice'):
            entry_price = float(entry_order['avgPrice'])
            order_side = entry_order['side']
            sl_tp_message = place_sl_tp_orders(order_side, entry_price)
            final_tg_message = (
                f"✅ **New Trade Placed!** ✅\n\n"
                f"**Signal:** {signal_type}\n**Ticker:** {TRADE_SYMBOL}\n\n"
                f"**Entry:** ${entry_price}\n**Qty:** {quantity}\n\n"
                f"**Status:**\n{sl_tp_message}"
            )
            status_code = 200
        else:
            final_tg_message = (
                f"❌ **Trade Failed!** ❌\n\n"
                f"**Signal:** {signal_type}\n**Ticker:** {TRADE_SYMBOL}\n**Qty:** {quantity}\n\n"
                f"**Binance Error:** {entry_message or 'Order fail/no avgPrice.'}"
            )
            status_code = 500

        send_telegram_message(final_tg_message)
        return jsonify({"status": "processed", "binance_message": entry_message if not entry_order else "OK"}), status_code

    except Exception as e:
        logging.exception(f"FATAL ERROR in webhook: {e}") # Log full traceback
        send_telegram_message(f"🚨 **FATAL BOT ERROR** 🚨\nCheck logs.")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

# --- WSGI Entry Point (for Gunicorn) ---
# Need wsgi.py: from server import app

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        logging.warning("Waitress not found, using Flask dev server (NOT FOR PRODUCTION).")
        app.run(host="0.0.0.0", port=port)
