from flask import Flask, request, jsonify
import requests
import os
from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# --- STRATEGY CONFIGURATION ---
TRADE_SYMBOL = "BTCUSDT"  # The symbol we are trading on Binance
LEVERAGE = 125
FIXED_STOP_LOSS_POINTS = 200  # $200
FIXED_TAKE_PROFIT_POINTS = 1300 # $1300

# NEW: Define the quantities you want to block
BLOCKED_QUANTITIES = {0.05, 0.075, 0.1, 0.125, 0.3, 0.4}
QUANTITY_CEILING = 0.475

# --- INITIALIZE BINANCE CLIENT ---
try:
    binance_client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    binance_client.FUTURES_URL = 'https://fapi.binance.com'
    # Check server time to confirm connection
    server_time = binance_client.futures_time()
    logging.info(f"Successfully connected to Binance Futures. Server time: {server_time['serverTime']}")
except Exception as e:
    binance_client = None
    logging.error(f"FATAL: Could not initialize Binance Client. Error: {e}")

# --- HELPER FUNCTIONS ---
def send_telegram_message(message):
    """Sends a message to the configured Telegram chat."""
    if not BOT_TOKEN or not CHAT_ID:
        logging.warning("Telegram BOT_TOKEN or CHAT_ID not set. Cannot send message.")
        return
    try:
        TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        response = requests.post(TELEGRAM_URL, json=payload)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")

def set_leverage(symbol, leverage):
    """Sets the leverage for a given symbol."""
    if not binance_client: return False, "Binance client not initialized."
    try:
        binance_client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage for {symbol} set to {leverage}x.")
        return True, f"Leverage set to {leverage}x."
    except BinanceAPIException as e:
        # Error code -4046 means "No need to change leverage"
        if e.code == -4046:
            logging.info(f"Leverage for {symbol} is already {leverage}x.")
            return True, f"Leverage is already {leverage}x."
        logging.error(f"Error setting leverage for {symbol}: {e}")
        return False, f"Failed to set leverage: {e.message}"

def place_entry_order(signal, quantity):
    """Places the initial market order to enter a position."""
    if not binance_client: return None, "Binance Client not initialized."
    try:
        trade_side = Client.SIDE_BUY if signal.upper() == 'LONG' else Client.SIDE_SELL
        
        logging.info(f"Attempting to place FUTURES entry order: {trade_side} {quantity} of {TRADE_SYMBOL}")
        order = binance_client.futures_create_order(
            symbol=TRADE_SYMBOL,
            side=trade_side,
            type=Client.ORDER_TYPE_MARKET,
            quantity=quantity)
        logging.info(f"Binance Futures entry successful: {order}")
        return order, "Futures entry order placed successfully."
    except Exception as e:
        logging.error(f"Binance Futures API Error on entry: {str(e)}")
        return None, f"Binance Futures API Error: {str(e)}"

def place_sl_tp_orders(entry_price, side):
    """Places fixed Stop-Loss and Take-Profit orders after entry."""
    if not binance_client: return "Binance Client not initialized."
    
    is_long = side.upper() == Client.SIDE_BUY
    stop_loss_price = round(entry_price - FIXED_STOP_LOSS_POINTS if is_long else entry_price + FIXED_STOP_LOSS_POINTS, 2)
    take_profit_price = round(entry_price + FIXED_TAKE_PROFIT_POINTS if is_long else entry_price - FIXED_TAKE_PROFIT_POINTS, 2)
    close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY

    sl_tp_status = ""
    try:
        # Place Stop Loss Order
        logging.info(f"Placing STOP_MARKET order at {stop_loss_price}")
        binance_client.futures_create_order(
            symbol=TRADE_SYMBOL,
            side=close_side,
            type='STOP_MARKET',
            stopPrice=stop_loss_price,
            reduceOnly=True,
            closePosition=True)
        sl_tp_status += f"✅ Stop-Loss set at ${stop_loss_price}\n"
    except Exception as e:
        logging.error(f"Error placing Stop-Loss order: {e}")
        sl_tp_status += f"❌ Failed to set Stop-Loss: {e}\n"

    try:
        # Place Take Profit Order
        logging.info(f"Placing TAKE_PROFIT_MARKET order at {take_profit_price}")
        binance_client.futures_create_order(
            symbol=TRADE_SYMBOL,
            side=close_side,
            type='TAKE_PROFIT_MARKET',
            stopPrice=take_profit_price,
            reduceOnly=True,
            closePosition=True)
        sl_tp_status += f"✅ Take-Profit set at ${take_profit_price}"
    except Exception as e:
        logging.error(f"Error placing Take-Profit order: {e}")
        sl_tp_status += f"❌ Failed to set Take-Profit: {e}"
        
    return sl_tp_status

def close_all_positions():
    """Closes all open futures positions for the trade symbol."""
    if not binance_client: return False, "Binance Client not initialized."
    try:
        positions = binance_client.futures_position_information(symbol=TRADE_SYMBOL)
        target_position = positions[0]

        if target_position and float(target_position['positionAmt']) != 0:
            quantity = abs(float(target_position['positionAmt']))
            is_long = float(target_position['positionAmt']) > 0
            close_side = Client.SIDE_SELL if is_long else Client.SIDE_BUY
            
            logging.info(f"Attempting to close FUTURES position: {close_side} {quantity} of {TRADE_SYMBOL}")
            order = binance_client.futures_create_order(
                symbol=TRADE_SYMBOL,
                side=close_side,
                type=Client.ORDER_TYPE_MARKET,
                quantity=quantity,
                reduceOnly=True)
            logging.info(f"Binance Futures close order successful: {order}")
            return True, f"Position for {TRADE_SYMBOL} closed successfully."
        else:
            logging.info(f"No open futures position found for {TRADE_SYMBOL}.")
            return True, f"No open position to close for {TRADE_SYMBOL}."
    except Exception as e:
        logging.error(f"An unexpected error occurred on close: {str(e)}")
        return False, f"An unexpected error occurred on close: {str(e)}"

# --- FLASK ROUTES ---
@app.route('/')
def health_check():
    return "OK", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        message = request.data.decode('utf-8')
        logging.info(f"Received webhook message: {message}")

        parts = {item.split(':')[0].strip(): item.split(':', 1)[1].strip() for item in message.split(',')}
        action = parts.get('action', 'message').strip()

        if action == 'close':
            success, result_message = close_all_positions()
            status_prefix = "✅" if success else "❌"
            tg_message = (f"{status_prefix} **Position Close Signal Received**\n\n"
                          f"**Binance Status:** {result_message}")
            send_telegram_message(tg_message)
            return jsonify({"status": "close signal processed"}), 200

        # --- Process LONG/SHORT signals ---
        signal_type = parts.get('signal', '').upper().strip()
        if signal_type not in ['LONG', 'SHORT']:
            logging.warning(f"Ignoring message with unknown signal type: {signal_type}")
            return jsonify({"status": "ignored, unknown signal"}), 200

        try:
            quantity = float(parts.get('qty', '0').strip())
        except ValueError:
            logging.error(f"Could not parse quantity from message: {parts.get('qty', '')}")
            send_telegram_message(f"❌ **Trade Failed!**\nCould not parse quantity from alert.")
            return jsonify({"status": "error", "message": "invalid quantity"}), 400

        # --- APPLYING YOUR NEW FILTERS ---
        if quantity in BLOCKED_QUANTITIES:
            logging.warning(f"IGNORING TRADE: Quantity {quantity} is on the block list.")
            send_telegram_message(f"🚫 **Trade Ignored**\n\n**Reason:** Quantity `{quantity}` is on the block list.")
            return jsonify({"status": "ignored", "reason": "blocked quantity"}), 200

        if quantity > QUANTITY_CEILING:
            logging.warning(f"IGNORING TRADE: Quantity {quantity} is above the ceiling of {QUANTITY_CEILING}.")
            send_telegram_message(f"🚫 **Trade Ignored**\n\n**Reason:** Quantity `{quantity}` is over the ceiling of `{QUANTITY_CEILING}`.")
            return jsonify({"status": "ignored", "reason": "quantity too high"}), 200
        
        # --- EXECUTE THE TRADE ---
        # 1. Set Leverage
        set_leverage(TRADE_SYMBOL, LEVERAGE)

        # 2. Place Entry Order
        entry_order, entry_message = place_entry_order(signal_type, quantity)

        if entry_order:
            # 3. If Entry is successful, get entry price and place SL/TP
            entry_price = float(entry_order['avgPrice'])
            sl_tp_message = place_sl_tp_orders(entry_price, entry_order['side'])
            
            # 4. Send success message to Telegram
            final_tg_message = (
                f"✅ **New Automated Trade Placed!** ✅\n\n"
                f"**Signal:** {signal_type}\n"
                f"**Ticker:** {TRADE_SYMBOL}\n\n"
                f"**Entry Price:** ${entry_price}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Binance Status:**\n{sl_tp_message}"
            )
        else:
            # 4. Send failure message to Telegram
            final_tg_message = (
                f"❌ **Trade Failed!** ❌\n\n"
                f"**Signal:** {signal_type}\n"
                f"**Ticker:** {TRADE_SYMBOL}\n"
                f"**Quantity:** {quantity}\n\n"
                f"**Binance Error:** {entry_message}"
            )

        send_telegram_message(final_tg_message)
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logging.error(f"FATAL ERROR in webhook processing: {e}")
        send_telegram_message(f"🚨 **FATAL BOT ERROR** 🚨\n\nThe server encountered a critical error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
