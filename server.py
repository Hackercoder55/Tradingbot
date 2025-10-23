from flask import Flask, request, jsonify
import requests
import os
# --- NEW BINANCE LIBRARY IMPORTS ---
from binance.um_futures import UMFutures # For USD(S)-M Futures
from binance.lib.utils import config_logging
from binance.error import ClientError # For specific Binance errors
# --- End of NEW IMPORTS ---
import logging
import json
from dotenv import load_dotenv

load_dotenv() # Explicitly load .env file variables

# --- Basic Logging Setup ---
# config_logging(logging, logging.INFO) # Optional: Use binance-connector's logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- SECRET KEYS & CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")

# --- STRATEGY CONFIGURATION ---
TRADE_SYMBOL = "BTCUSDC"
LEVERAGE = 125
FIXED_STOP_LOSS_POINTS = 200
FIXED_TAKE_PROFIT_POINTS = 1300

# --- INITIALIZE BINANCE CLIENT (using binance-connector) ---
binance_client = None # Initialize as None
try:
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise ValueError("Binance API Key or Secret not found.")

    # Initialize the UMFutures client
    binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
    # Test connection by getting server time
    server_time = binance_client.time()
    logging.info(f"Successfully connected to Binance Futures (using binance-connector). Server time: {server_time['serverTime']}")

except ClientError as ce:
    binance_client = None
    logging.error(f"FATAL: Binance API Error during startup (binance-connector): Status={ce.status_code}, Code={ce.error_code}, Msg={ce.error_message}")
except Exception as e:
    binance_client = None
    logging.error(f"FATAL: Could not initialize Binance Client during startup (binance-connector). Error: {e}")

# --- HELPER FUNCTIONS ---
def send_telegram_message(message):
    # Sends a message to the configured Telegram chat.
    if not BOT_TOKEN or not CHAT_ID:
        logging.warning("Telegram BOT_TOKEN or CHAT_ID not set.")
        return
    try:
        TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
        response = requests.post(TELEGRAM_URL, json=payload)
        response.raise_for_status() # Raise exception for bad status codes
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}")

def set_leverage(symbol, leverage):
    """Sets leverage using binance-connector."""
    if not binance_client: return False, "Binance client not initialized."
    try:
        response = binance_client.change_leverage(symbol=symbol, leverage=leverage)
        logging.info(f"Leverage change response for {symbol}: {response}")
        # Check specific message for confirmation it's already set
        if response.get('leverage') == leverage:
             return True, f"Leverage set to {leverage}x (or already was)."
        else:
             # This case might indicate an issue, but we proceed assuming it worked if no exception
             logging.warning(f"Leverage response did not explicitly confirm {leverage}x, but no error.")
             return True, f"Leverage change requested to {leverage}x."

    except ClientError as ce:
        # Check if the error message indicates leverage is already set
        if "No need to change leverage" in ce.error_message:
             logging.info(f"Leverage for {symbol} is already {leverage}x.")
             return True, f"Leverage is already {leverage}x."
        logging.error(f"Binance API Error setting leverage: Status={ce.status_code}, Code={ce.error_code}, Msg={ce.error_message}")
        return False, f"Failed leverage: {ce.error_message}"
    except Exception as e:
        logging.error(f"Unexpected error setting leverage: {e}")
        return False, f"Unexpected error setting leverage: {str(e)}"

def place_entry_order(signal, quantity):
    """Places entry market order using binance-connector."""
    if not binance_client: return None, "Binance Client not initialized."
    try:
        trade_side = "BUY" if signal.upper() == 'BUY' else "SELL"
        logging.info(f"Attempting to place FUTURES entry order: {trade_side} {quantity} of {TRADE_SYMBOL}")
        order = binance_client.new_order(
            symbol=TRADE_SYMBOL,
            side=trade_side,
            type="MARKET",
            quantity=quantity
        )
        logging.info(f"Binance Futures entry order response: {order}")

        # Check order status - 'FILLED' is ideal, but market orders fill quickly
        if order.get('orderId') and order.get('status') in ['NEW', 'FILLED', 'PARTIALLY_FILLED']:
            # For market orders, avgPrice might not be in initial response.
            # We'll try to get it, otherwise return order ID for later checks if needed.
            # Best practice is often to query the order after a short delay if precise fill price needed immediately.
            # For SL/TP placement, using a reasonable estimate or querying order might be needed.
            # Let's try calculating from cumQuote and executedQty if available.
            avg_price_str = order.get('avgPrice', '0')
            if float(avg_price_str) > 0:
                 logging.info(f"Order filled with avgPrice: {avg_price_str}")
                 return order, "Futures entry order placed successfully."
            else:
                try:
                    executed_qty = float(order.get('executedQty', 0))
                    cum_quote = float(order.get('cumQuote', 0))
                    if executed_qty > 0:
                        avg_price_calc = cum_quote / executed_qty
                        order['avgPrice'] = str(avg_price_calc) # Add calculated avgPrice
                        logging.info(f"Calculated avgPrice: {avg_price_calc}")
                        return order, "Futures entry order placed (avgPrice calculated)."
                    else:
                        logging.warning(f"Market order response received but executedQty is 0: {order}")
                        return order, f"Order placed (ID: {order.get('orderId')}), but fill details pending or quantity was zero."
                except Exception as calc_e:
                     logging.error(f"Could not calculate avgPrice from response: {calc_e}. Order details: {order}")
                     return order, f"Order placed (ID: {order.get('orderId')}), but fill price unknown."
        else:
            logging.error(f"Order placement failed or returned unexpected status: {order}")
            return None, f"Order placement failed. Status: {order.get('status', 'N/A')}. Reason: {order.get('msg', 'Unknown')}"

    except ClientError as ce:
        logging.error(f"Binance API Error placing entry order: Status={ce.status_code}, Code={ce.error_code}, Msg={ce.error_message}")
        return None, f"Binance API Error: {ce.error_message}"
    except Exception as e:
        logging.exception(f"Unexpected error placing entry order: {e}") # Log full traceback
        return None, f"Unexpected error placing entry order: {str(e)}"

def place_sl_tp_orders(side, entry_price):
    """Places SL/TP orders using binance-connector."""
    if not binance_client: return "Binance Client not initialized."
    is_long = side.upper() == "BUY"
    # Ensure prices have correct precision for the symbol (e.g., BTCUSDC might need 1 decimal place)
    # Fetch precision from exchange info if needed, assuming 2 for now.
    stop_loss_price_str = f"{entry_price - FIXED_STOP_LOSS_POINTS if is_long else entry_price + FIXED_STOP_LOSS_POINTS:.1f}" # Adjusted precision to .1f for BTCUSDC
    take_profit_price_str = f"{entry_price + FIXED_TAKE_PROFIT_POINTS if is_long else entry_price - FIXED_TAKE_PROFIT_POINTS:.1f}" # Adjusted precision to .1f for BTCUSDC
    close_side = "SELL" if is_long else "BUY"
    sl_tp_status = ""

    # Cancel existing SL/TP first
    try:
        logging.info(f"Attempting to cancel existing SL/TP orders for {TRADE_SYMBOL}")
        open_orders = binance_client.get_open_orders(symbol=TRADE_SYMBOL)
        order_ids_to_cancel = [
            order['orderId'] for order in open_orders
            if order.get('type') in ['STOP_MARKET', 'TAKE_PROFIT_MARKET']
        ]
        if order_ids_to_cancel:
            for order_id in order_ids_to_cancel:
                 try:
                      binance_client.cancel_order(symbol=TRADE_SYMBOL, orderId=order_id)
                      logging.info(f"Cancelled existing SL/TP order ID: {order_id}")
                 except ClientError as cancel_ce:
                      # Ignore errors if order already filled/cancelled
                      if cancel_ce.error_code == -2011:
                           logging.warning(f"Order {order_id} likely already filled/cancelled: {cancel_ce.error_message}")
                      else:
                           raise # Re-raise other cancellation errors
                 except Exception as cancel_e:
                      logging.warning(f"Could not cancel order {order_id}: {cancel_e}")
        else:
             logging.info("No existing SL/TP orders found to cancel.")
    except ClientError as ce:
         logging.warning(f"Could not get open orders to cancel (maybe none): Status={ce.status_code}, Msg={ce.error_message}")
    except Exception as e:
        logging.warning(f"Could not check/cancel existing orders: {e}")

    # Place new SL
    try:
        logging.info(f"Placing STOP_MARKET order trigger at {stop_loss_price_str}")
        sl_order = binance_client.new_order(
            symbol=TRADE_SYMBOL,
            side=close_side,
            type='STOP_MARKET',
            stopPrice=stop_loss_price_str,
            closePosition=True,
            timeInForce='GTC'
        )
        logging.info(f"Stop loss order response: {sl_order}")
        sl_tp_status += f"✅ Stop-Loss target: ${stop_loss_price_str}\n"
    except ClientError as ce:
        logging.error(f"Binance API Error placing SL: Status={ce.status_code}, Code={ce.error_code}, Msg={ce.error_message}")
        sl_tp_status += f"❌ Failed SL: {ce.error_message}\n"
    except Exception as e:
        logging.exception(f"Unexpected error placing SL: {e}")
        sl_tp_status += f"❌ Unexpected SL error: {str(e)}\n"

    # Place new TP
    try:
        logging.info(f"Placing TAKE_PROFIT_MARKET order trigger at {take_profit_price_str}")
        tp_order = binance_client.new_order(
            symbol=TRADE_SYMBOL,
            side=close_side,
            type='TAKE_PROFIT_MARKET',
            stopPrice=take_profit_price_str,
            closePosition=True,
            timeInForce='GTC'
        )
        logging.info(f"Take profit order response: {tp_order}")
        sl_tp_status += f"✅ Take-Profit target: ${take_profit_price_str}"
    except ClientError as ce:
        logging.error(f"Binance API Error placing TP: Status={ce.status_code}, Code={ce.error_code}, Msg={ce.error_message}")
        sl_tp_status += f"❌ Failed TP: {ce.error_message}"
    except Exception as e:
        logging.exception(f"Unexpected error placing TP: {e}")
        sl_tp_status += f"❌ Unexpected TP error: {str(e)}"
    return sl_tp_status


# --- FLASK ROUTES ---
@app.route('/')
def health_check():
    # Health check for the web server itself
    return "Bot server is running.", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    global binance_client # Allow modifying the global client if re-initialization is needed
    try:
        # Check if client failed during startup and try re-initializing
        if binance_client is None:
            logging.error("Webhook received but Binance client is not initialized.")
            try:
                logging.info("Attempting to re-initialize Binance client...")
                if not BINANCE_API_KEY or not BINANCE_API_SECRET:
                     raise ValueError("API keys missing.")
                binance_client = UMFutures(key=BINANCE_API_KEY, secret=BINANCE_API_SECRET)
                server_time = binance_client.time()
                logging.info(f"Re-initialization successful. Server time: {server_time['serverTime']}")
                send_telegram_message("✅ Bot recovered connection to Binance.")
            except Exception as reinit_e:
                logging.error(f"Re-initialization failed: {reinit_e}")
                send_telegram_message(f"🚨 BOT ERROR: Still unable to connect to Binance.")
                return jsonify({"status": "error", "message": "Binance client failed initialization"}), 500

        # --- PARSE JSON DATA ---
        try:
            data = request.get_json()
            if not data or not isinstance(data, dict):
                 raise ValueError("Expected valid JSON data.")
            logging.info(f"Received webhook JSON data: {data}")
        except Exception as parse_error:
            logging.error(f"Could not parse request JSON data: {parse_error}")
            return jsonify({"status": "error", "message": "Could not parse JSON"}), 400

        # --- Extract action ('BUY' or 'SELL') ---
        signal_type = data.get('action', '').upper().strip()
        if signal_type not in ['BUY', 'SELL']:
            logging.warning(f"Ignoring: Invalid 'action': {signal_type}")
            return jsonify({"status": "ignored, invalid action"}), 200

        # --- Extract quantity ---
        try:
            quantity = float(data.get('qty', 0))
            if quantity <= 0: raise ValueError("Qty must be > 0.")
        except (ValueError, TypeError) as qty_error:
            logging.error(f"Invalid quantity: {data.get('qty')}. Error: {qty_error}")
            send_telegram_message(f"❌ **Trade Failed!**\nInvalid qty: `{data.get('qty')}`")
            return jsonify({"status": "error", "message": f"Invalid qty: {qty_error}"}), 400

        # --- EXECUTE THE TRADE ---
        leverage_success, leverage_message = set_leverage(TRADE_SYMBOL, LEVERAGE)
        if not leverage_success:
            send_telegram_message(f"❌ **Trade Failed!**\nLeverage Error.\n**Binance:** {leverage_message}")
            return jsonify({"status": "error", "message": "Failed leverage"}), 500

        entry_order, entry_message = place_entry_order(signal_type, quantity)

        # Check if entry_order exists and contains 'avgPrice' or calculated avgPrice
        avg_price_str = entry_order.get('avgPrice') if entry_order else None

        if avg_price_str and float(avg_price_str) > 0:
            entry_price = float(avg_price_str)
            order_side = entry_order.get('side')
            if not order_side: # Fallback
                 order_side = "BUY" if signal_type == "BUY" else "SELL"
                 logging.warning("Order 'side' not found in response, using signal_type.")

            sl_tp_message = place_sl_tp_orders(order_side, entry_price)

            final_tg_message = (
                f"✅ **New Trade Placed!** ✅\n\n"
                f"**Signal:** {signal_type}\n**Ticker:** {TRADE_SYMBOL}\n\n"
                f"**Entry:** ${entry_price:.1f}\n**Qty:** {quantity}\n\n" # Format entry price .1f for BTCUSDC
                f"**Status:**\n{sl_tp_message}"
            )
            status_code = 200
            response_status = "success"
        else:
            # Handle cases where order might be placed but not filled / avgPrice not returned
            order_id_msg = f" (Order ID: {entry_order.get('orderId')})" if entry_order else ""
            final_tg_message = (
                f"❌ **Trade Failed!** ❌\n\n"
                f"**Signal:** {signal_type}\n**Ticker:** {TRADE_SYMBOL}\n**Qty:** {quantity}\n\n"
                f"**Binance Error:** {entry_message or f'Order placement issue{order_id_msg}. Check Binance.'}"
            )
            status_code = 500
            response_status = "error"

        send_telegram_message(final_tg_message)
        return jsonify({"status": response_status, "binance_message": entry_message or "Unknown error"}), status_code

    except Exception as e:
        logging.exception(f"FATAL ERROR in webhook: {e}")
        send_telegram_message(f"🚨 **FATAL BOT ERROR** 🚨\nCheck logs.")
        return jsonify({"status": "error", "message": "Internal server error"}), 500
        
