from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# ESP32 IP and Port for OLED and Servo control
ESP32_IP = "http://192.168.52.133"
ESP32_PORT = 80

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project5'
}

# List of servo pins (IDs) used in the system
SERVO_PINS = [12, 13, 14, 26, 27]
servo_cache = {}  # Cache to store the last known state of servos

# Variables for error logging and status messages
last_error_message = None
error_occurrence_count = 0
normal_status_printed = False

# Global variable to store the item awaiting drop (alternative to session for sensor trigger)
# This assumes a single-user/single-device scenario where only one item is "active" for dropping at a time.
current_item_awaiting_drop = None

def get_db_connection():
    """
    Establishes a database connection using the global DB_CONFIG.
    Returns the connection object or None if an error occurs.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

def send_oled_text(lines):
    """
    Sends text to the ESP32 for display on the OLED screen.
    Formats the text to a maximum of 5 lines, each with a maximum of 20 characters.
    """
    try:
        # Truncate each line to 20 characters and take up to 5 lines
        formatted_lines = [line[:20] for line in lines[:5]]
        payload = '\n'.join(formatted_lines)  # Join lines with newline character
        # Send the payload to the ESP32's /oled endpoint
        response = requests.post(f"{ESP32_IP}/oled", data=payload, headers={'Content-Type': 'text/plain'}, timeout=5)
        print(f"OLED display updated: {response.text}")
    except requests.exceptions.RequestException as e:
        # Log any errors during the request to ESP32
        print(f"Error sending data to ESP32's OLED: {e}")

def check_and_control_servo_loop():
    """
    Continuously monitors servo states from the database and sends control commands to the ESP32.
    Handles database and network errors, providing robust logging and recovery messages.
    This function runs in a separate thread.
    """
    global servo_cache
    global last_error_message, error_occurrence_count, normal_status_printed
    while True:
        try:
            conn = get_db_connection()
            if not conn:
                # If database connection fails, raise an exception to be caught
                raise Exception("Failed to get database connection in servo loop.")
            cursor = conn.cursor(dictionary=True)
            # Fetch current servo states from tbl_bin
            cursor.execute("SELECT id, value FROM tbl_bin")
            rows = cursor.fetchall()
            conn.close()
            # Log system recovery or normal operation status
            if last_error_message is not None:
                print(f"[Recovered] System is back to normal after error: {last_error_message} ({error_occurrence_count} times)")
                last_error_message = None
                error_occurrence_count = 0
                normal_status_printed = True
            elif not normal_status_printed:
                print("[Status] System is operating normally")
                normal_status_printed = True
            # Iterate through fetched servo states and control ESP32 if state has changed
            for row in rows:
                servo_id = row["id"]
                value = row["value"]
                action = "on" if value == 1 else "off"
                if servo_id not in servo_cache or servo_cache[servo_id] != value:
                    try:
                        # Send command to ESP32 to control the servo
                        requests.get(f"{ESP32_IP}/servo{servo_id}/{action}", timeout=2)
                        print(f"[Update] Servo {servo_id} → {action.upper()}")
                        servo_cache[servo_id] = value  # Update cache
                    except requests.exceptions.RequestException as req_e:
                        # Handle ESP32 communication errors
                        current_error = f"Cannot control ESP32 for Servo {servo_id} - {req_e}"
                        if current_error != last_error_message:
                            print(f"[Error] {current_error}")
                            last_error_message = current_error
                            error_occurrence_count = 1
                            normal_status_printed = False
                        else:
                            error_occurrence_count += 1
        except mysql.connector.Error as db_err:
            # Handle database-specific errors
            current_error = f"Database error: {db_err}"
            if current_error != last_error_message:
                print(f"[Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1
        except Exception as e:
            # Handle any other unexpected errors in the loop
            current_error = f"General error in loop: {e}"
            if current_error != last_error_message:
                print(f"[Loop Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1
        finally:
            # Ensure a small delay before the next iteration
            time.sleep(0.1)

def role_required(allowed_roles):
    """
    Decorator to restrict access to Flask routes based on user roles stored in the session.
    If no role is set, it defaults to 'root_admin' for initial setup.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session:
                # Default role for initial setup/testing
                session['role'] = 'root_admin'
                session['email'] = 'test@example.com'
            user_role = session.get('role')
            if user_role not in allowed_roles:
                flash("You do not have permission to access this page.", 'danger')
                return redirect(url_for('bin'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# Helper function for disquantity logic and state management
def _handle_disquantity_logic(conn, cursor, is_sensor_trigger=False, flash_func=None):
    global current_item_awaiting_drop
    """
    Handles the logic for incrementing disquantity and managing active item data.
    Args:
        conn: Database connection object.
        cursor: Database cursor.
        is_sensor_trigger (bool): True if called by sensor, False if by button.
        flash_func (callable): Flask's flash function (only for button, pass None for sensor).
    """

    item_to_process = None
    if is_sensor_trigger:
        item_to_process = current_item_awaiting_drop
        # Do NOT clear global variable immediately. It will be cleared after checking remaining items.
    else: # Button press, relies on session
        item_to_process = session.get('stored_item_for_increment')
        session.pop('stored_item_for_increment', None) # Clear session for button

    if item_to_process:
        barcode_id_to_search = item_to_process['barcode_id']
        products_id_to_disquantity = item_to_process['products_id']
        product_name = item_to_process.get('products_name', 'UNKNOWN ITEM') # Get product name from stored item

        # Fetch the order item to update its disquantity
        cursor.execute("""
            SELECT id, quantity, disquantity, products_name
            FROM tbl_order
            WHERE barcode_id = %s AND products_id = %s
        """, (barcode_id_to_search, products_id_to_disquantity))
        order_item_to_update = cursor.fetchone()

        if order_item_to_update:
            # Access dictionary keys directly now
            order_id = order_item_to_update['id']
            current_quantity = order_item_to_update['quantity']
            current_disquantity = order_item_to_update['disquantity']
            product_name = order_item_to_update['products_name'] # Get from fetched data for accuracy
            proposed_disquantity = current_disquantity + 1

            if proposed_disquantity <= current_quantity:
                # Increment disquantity in the database
                cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                               (proposed_disquantity, order_id))
                
                if is_sensor_trigger:
                    print(f"[Disquantity Update] Increased disquantity for item '{product_name}' to {proposed_disquantity}")
                    
                    # OLED display for successful refund
                    send_oled_text([f"{product_name[:20]}", "REFUNDED"]) # Truncate product name for OLED
                    time.sleep(2) # Display message for 2 seconds

                    # Re-fetch totals for the barcode to get current state
                    cursor.execute("""
                        SELECT SUM(quantity) AS total_quantity, SUM(disquantity) AS total_disquantity
                        FROM tbl_order
                        WHERE barcode_id = %s
                    """, (barcode_id_to_search,))
                    current_totals = cursor.fetchone()
                    
                    if current_totals and current_totals['total_quantity'] is not None:
                        total_quantity = current_totals['total_quantity']
                        total_disquantity = current_totals['total_disquantity']
                        remaining = total_quantity - total_disquantity

                        # OLED Display: Barcode summary
                        send_oled_text([
                            f"BARCODE:{barcode_id_to_search[:12]}",
                            f"REM:{remaining} TOTAL:{total_quantity}",
                            f"REFUNDED:{total_disquantity}"
                        ])
                        print(f"[OLED Update] Displaying barcode summary for {barcode_id_to_search} after refund. Remaining: {remaining}")

                        # Clear current_item_awaiting_drop (this specific item is handled)
                        current_item_awaiting_drop = None
                        session['item_drop_pending'] = False # Mark as ready for next selection/scan

                        # If all items for this receipt are refunded, clear the receipt barcode from session
                        if remaining == 0:
                            session.pop('current_receipt_barcode', None)
                            print(f"[OLED Update] All items for barcode {barcode_id_to_search} refunded. Receipt barcode cleared.")
                    else:
                        # Fallback if for some reason totals can't be fetched after refund
                        send_oled_text(["ERROR: NO TOTALS", "SCAN RECEIPT"])
                        current_item_awaiting_drop = None
                        session.pop('current_receipt_barcode', None)
                        session['item_drop_pending'] = False
                        print("[OLED Update] Could not fetch totals after refund, reverting to scan receipt state.")
                else: # Button press (usually 'reset_all_servos' action which includes disquantity logic)
                    flash_func(f"Successfully increased discarded quantity for '{product_name}'.", 'success')
            else: # Proposed disquantity exceeds current quantity
                if is_sensor_trigger:
                    print(f"[Warning] Cannot increase disquantity for '{product_name}'. Reached maximum quantity.")
                    # OLED display for quantity exceeded
                    send_oled_text([f"ITEM '{product_name[:14]}'", "EXCEEDS QUANTITY.", "CONTACT STAFF."]) # Truncate product name
                    time.sleep(5) # Display warning for 5 seconds
                    send_oled_text(["SCAN RECEIPT", "BARCODE"]) # Return to initial state
                    current_item_awaiting_drop = None # Clear global var if error
                    session.pop('current_receipt_barcode', None)
                    session['item_drop_pending'] = False
                else: # Button press
                    flash_func(f"Cannot increase discarded quantity beyond existing product quantity for '{product_name}'.", 'danger')
        else: # No matching product found for update
            if is_sensor_trigger:
                print("[Warning] No matching product found for disquantity increment (sensor).")
                # OLED display for no matching product
                send_oled_text(["NO ACTIVE ITEM", "SCAN RECEIPT"])
                time.sleep(3) # Display for 3 seconds
                send_oled_text(["SCAN RECEIPT", "BARCODE"]) # Return to initial state
                current_item_awaiting_drop = None # Clear global var if no active item
                session.pop('current_receipt_barcode', None)
                session['item_drop_pending'] = False
            else: # Button press
                flash_func("No matching product found for incrementing discarded quantity.", 'danger')
    else: # No active item
        if is_sensor_trigger:
            print("[Warning] No active item awaiting drop (sensor). Disquantity not incremented.")
            # OLED display for no active item
            send_oled_text(["NO ACTIVE ITEM", "SCAN RECEIPT"])
            time.sleep(3) # Display for 3 seconds
            send_oled_text(["SCAN RECEIPT", "BARCODE"]) # Return to initial state
            current_item_awaiting_drop = None # Clear global var if no active item
            session.pop('current_receipt_barcode', None)
            session['item_drop_pending'] = False
        else: # Button press
            flash_func("No product selected for incrementing discarded quantity. Only resetting Servo status.", 'info')


@app.route('/toggle_servo/<int:servo_id>', methods=['POST'])
def toggle_servo(servo_id):
    """
    Toggles the state of a specific servo in the database (sets its value to 1 and others to 0).
    This endpoint is typically called from the web UI.
    """
    if servo_id in SERVO_PINS:
        conn = get_db_connection()
        if not conn:
            flash("Error connecting to the database.", 'danger')
            return redirect(url_for('bin'))
        try:
            cursor = conn.cursor(dictionary=True)
            # Set all other servos to OFF (value = 0)
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE id != %s", (servo_id,))
            # Set the selected servo to ON (value = 1)
            cursor.execute("UPDATE tbl_bin SET value = 1 WHERE id = %s", (servo_id,))
            conn.commit()
            flash(f"Successfully turned on Servo {servo_id} and turned off all others.", 'success')
        except Exception as e:
            flash(f"Error toggling Servo status: {e}", 'danger')
            conn.rollback()  # Rollback changes if an error occurs
        finally:
            conn.close()
    else:
        flash(f"Invalid Servo ID {servo_id}.", 'danger')
    return redirect(url_for('bin'))

@app.route("/", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    global current_item_awaiting_drop
    """
    Handles the main bin management page.
    Allows searching for orders by barcode, incrementing discarded quantities,
    updating tbl_bin for servo control, and displaying current servo states.
    Also manages OLED display messages based on user actions.
    """

    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", 'danger')
        # If DB connection fails, display error on OLED
        send_oled_text(["DB CONNECTION", "FAILED!"])
        return render_template("bin.html", orders=[], barcode_id_filter='', request_form_data={}, servo_data={})
    
    cursor = conn.cursor(dictionary=True)
    orders_data = []
    # Get barcode_id_filter from URL parameters for persistence in the search box
    barcode_id_filter = request.args.get('barcode_id_filter', '')
    request_form_data = {}
    servo_data = {}
    
    try:
        # Fetch current servo states for display on the web page
        cursor.execute("SELECT id, value FROM tbl_bin")
        servo_rows = cursor.fetchall()
        servo_data = {row['id']: row['value'] for row in servo_rows}
    except mysql.connector.Error as err:
        flash(f"Error fetching Servo data: {err}", 'danger')
        send_oled_text(["DB ERROR", "SERVOS!"]) # OLED for servo data fetch error

    if request.method == 'POST':
        request_form_data = request.form.to_dict()
        action = request.form.get('action')
        
        if action == 'add_disquantity':
            # Action to prepare for incrementing discarded quantity
            barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
            products_id_to_disquantity = request.form.get('products_id_to_disquantity')
            if not barcode_id_to_search or not products_id_to_disquantity:
                flash("Please specify barcode ID and product ID to increment discarded quantity.", 'danger')
                send_oled_text(["MISSING INFO", "SCAN RECEIPT"]) # OLED for missing info
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
            try:
                # Retrieve product and category information for the selected item
                cursor.execute("""
                    SELECT p.category_id, o.products_name, o.quantity, o.disquantity
                    FROM tbl_order o
                    JOIN tbl_products p ON o.products_id = p.products_id
                    WHERE o.barcode_id = %s AND o.products_id = %s
                """, (barcode_id_to_search, products_id_to_disquantity))
                item_info = cursor.fetchone()
                if item_info:
                    category_id_for_bin = item_info.get('category_id')
                    product_name = item_info.get('products_name')
                    if category_id_for_bin is None:
                        flash(f"No category_id found for product '{product_name}'.", 'danger')
                        send_oled_text(["NO CATEGORY", "CONTACT STAFF"]) # OLED if no category
                        conn.close()
                        return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
                    
                    # Store item details in session (for button) AND global variable (for sensor)
                    session['stored_item_for_increment'] = {
                        'barcode_id': barcode_id_to_search,
                        'products_id': products_id_to_disquantity,
                        'category_id': category_id_for_bin
                    }
                    current_item_awaiting_drop = { # Set global variable for sensor
                        'barcode_id': barcode_id_to_search,
                        'products_id': products_id_to_disquantity,
                        'category_id': category_id_for_bin,
                        'products_name': product_name # Store product name here for sensor to use
                    }
                    
                    # Store current barcode in session for OLED persistence
                    session['current_receipt_barcode'] = barcode_id_to_search
                    session['item_drop_pending'] = True # Mark that an item is awaiting drop

                    # Update tbl_bin to open the correct bin (category_id)
                    cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s AND category_id IN (1, 2, 3, 4, 5)", (category_id_for_bin,))
                    cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))
                    conn.commit()
                    # OLED Display: Product name and category for dropping
                    send_oled_text([
                        f"ITEM: {product_name[:14]}",  # Truncate product name for OLED display
                        f"DROP IN BIN: {category_id_for_bin}"
                    ])
                else:
                    flash("No matching product found.", 'danger')
                    send_oled_text(["NO MATCHING", "PRODUCT"]) # OLED for no matching product
            except mysql.connector.Error as err:
                flash(f"Error during operation: {err}", 'danger')
                conn.rollback()
                send_oled_text(["DB ERROR", "SEE LOGS"]) # OLED for DB error
            finally:
                if conn:
                    conn.close()
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
        
        elif action == 'reset_all_servos':
            # Retrieve the barcode ID from the form to preserve it after the redirect
            barcode_id_filter_to_preserve = request.form.get('barcode_id_filter', '')
            conn_reset = get_db_connection()
            if not conn_reset:
                flash("Error connecting to the database to reset Servos.", 'danger')
                send_oled_text(["DB CONNECTION", "FAILED!"]) # OLED for DB error on reset
                return redirect(url_for('bin', barcode_id_filter=barcode_id_filter_to_preserve))
            try:
                cursor_reset = conn_reset.cursor(dictionary=True) # Ensure dictionary=True here
                
                # Call the common logic for disquantity update and session/global variable management
                # This call is for the button's purpose, it will not trigger sensor-specific OLED messages.
                _handle_disquantity_logic(
                    conn_reset,
                    cursor_reset,
                    is_sensor_trigger=False,
                    flash_func=flash
                )

                # Reset all bin values to 0 (servos OFF) in the database
                cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn_reset.commit() # This commits all changes including tbl_order (if updated by helper) and tbl_bin.

                # Explicitly send OFF commands to ESP32 for all servos from button press
                for sid in SERVO_PINS:
                    try:
                        requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                        print(f"[Forced Update - Button] Servo {sid} → OFF")
                        servo_cache[sid] = 0  # Update local cache
                    except requests.exceptions.RequestException as e:
                        print(f"[Error - Button] Cannot control ESP32 for Servo {sid}: {e}")
                
                # OLED Display for manual reset
                send_oled_text(["SYSTEM RESET.", "READY FOR NEW SCAN."])
                time.sleep(2) # Display for 2 seconds
                send_oled_text(["SCAN RECEIPT", "BARCODE"]) # Return to initial state
                session.pop('current_receipt_barcode', None) # Clear session on full reset
                session['item_drop_pending'] = False
                current_item_awaiting_drop = None # Clear global variable as well

            except mysql.connector.Error as err:
                flash(f"Error resetting Servos and increasing discarded quantity: {err}", 'danger')
                send_oled_text(["DB ERROR", "RESET FAILED!"]) # OLED for DB error on reset
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
                # Redirect back to the bin page with the preserved barcode ID
                return redirect(url_for('bin', barcode_id_filter=barcode_id_filter_to_preserve))
    
    # Handling GET requests (including 'reset_search' and initial page load)
    if request.method == 'GET':
        action = request.args.get('action')
        
        if action == 'reset_search':
            # Action when user clicks the 'รีเซ็ต' button in the search section
            print("[Reset Search Action] Clearing search filter, resetting servos, and OLED.")
            barcode_id_filter = '' # Clear the search filter
            request_form_data = {} # Clear any previous form data
            
            try:
                # Reset all bin values to 0 (servos OFF) in the database
                cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn.commit()

                # Explicitly send OFF commands to ESP32 for all servos
                for sid in SERVO_PINS:
                    try:
                        requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                        print(f"[Forced Update - Reset Search] Servo {sid} → OFF")
                        servo_cache[sid] = 0  # Update local cache
                    except requests.exceptions.RequestException as e:
                        print(f"[Error - Reset Search] Cannot control ESP32 for Servo {sid}: {e}")
                
                # OLED Display: Initial prompt for scanning receipt barcode
                send_oled_text(["SCAN RECEIPT", "BARCODE"])
                session.pop('current_receipt_barcode', None) # Clear session
                session.pop('stored_item_for_increment', None) # Clear any item waiting for drop from button
                session['item_drop_pending'] = False
                current_item_awaiting_drop = None # Clear global variable
                
                flash("การค้นหาถูกรีเซ็ตและ Servo ทั้งหมดถูกปิด", 'info')

            except mysql.connector.Error as err:
                flash(f"Error resetting Servos during search reset: {err}", 'danger')
                send_oled_text(["DB ERROR", "RESET FAILED!"])
                conn.rollback()
            except Exception as e:
                flash(f"General error during search reset: {e}", 'danger')
                send_oled_text(["ERROR", "RESET FAILED!"])
            finally:
                if conn:
                    conn.close()
                # Redirect back to the bin page with an empty barcode_id_filter
                return redirect(url_for('bin', barcode_id_filter=''))
        
        # --- Existing GET request logic (search and initial load) ---
        
        # Determine barcode_id to display from URL or session
        display_barcode_id = barcode_id_filter
        if not display_barcode_id and session.get('current_receipt_barcode'):
            display_barcode_id = session.get('current_receipt_barcode')

        if display_barcode_id:
            # Query to get all orders with the same barcode_id
            base_query = """
                SELECT o.*, p.price, p.products_name, p.category_id
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s
            """
            query_params = [display_barcode_id]
            if session.get('role') == 'member':
                base_query += " AND o.email = %s"
                query_params.append(session['email'])
            base_query += " ORDER BY o.id DESC"
            cursor.execute(base_query, tuple(query_params))
            orders_data = cursor.fetchall()
            # Query to get total quantity and disquantity for the barcode
            total_query = """
                SELECT SUM(quantity) AS total_quantity, SUM(disquantity) AS total_disquantity
                FROM tbl_order
                WHERE barcode_id = %s
            """
            total_query_params = [display_barcode_id]
            cursor.execute(total_query, tuple(total_query_params))
            totals = cursor.fetchone()

            if orders_data and totals and totals['total_quantity'] is not None:
                # If an item is currently awaiting drop, do not change OLED from 'DROP IN BIN'
                # This ensures the instruction for dropping is persistent on OLED.
                if not session.get('item_drop_pending'):
                    # Only update OLED if no item is currently active for dropping
                    total_quantity = totals['total_quantity']
                    total_disquantity = totals['total_disquantity']
                    remaining = total_quantity - total_disquantity
                    send_oled_text([
                        f"BARCODE:{display_barcode_id[:12]}",
                        f"REM:{remaining} TOTAL:{total_quantity}",
                        f"REFUNDED:{total_disquantity}"
                    ])
                    time.sleep(2)
            else:
                # No order found for this barcode filter (or data became empty)
                send_oled_text(["NO ORDER FOUND", "SCAN NEW RECEIPT"])
                session.pop('current_receipt_barcode', None)
                session['item_drop_pending'] = False
                current_item_awaiting_drop = None # Clear global variable
        else:
            orders_data = []
            # OLED Display: Initial prompt for scanning receipt barcode when no filter is active
            send_oled_text(["SCAN RECEIPT", "BARCODE"])
            session.pop('current_receipt_barcode', None)
            session['item_drop_pending'] = False
            current_item_awaiting_drop = None # Clear global variable
    
    # Main logic for displaying order table based on barcode_id_filter (if not redirected earlier)
    # This block will only execute if it's a normal GET request (not 'reset_search' or 'search' POST)
    if 'conn' in locals() and conn.is_connected(): # Check if connection is still open from earlier GET attempts
        conn.close() # Close connection if it's still open from earlier GET attempts
        conn = get_db_connection() # Re-open if needed for final render data
        if conn:
            cursor = conn.cursor(dictionary=True)
            # Re-fetch servo data for the final render, in case it was updated by the reset
            cursor.execute("SELECT id, value FROM tbl_bin")
            servo_rows = cursor.fetchall()
            servo_data = {row['id']: row['value'] for row in servo_rows}
            conn.close() # Close again after fetching final servo data
    else: # If connection was closed or never opened, ensure servo_data is consistent
        if not servo_data: # If servo_data is still empty, try to fetch it again if possible
            conn_temp = get_db_connection()
            if conn_temp:
                cursor_temp = conn_temp.cursor(dictionary=True)
                cursor_temp.execute("SELECT id, value FROM tbl_bin")
                servo_rows_temp = cursor_temp.fetchall()
                servo_data = {row['id']: row['value'] for row in servo_rows_temp}
                conn_temp.close()


    current_auto_order_id = "N/A"  # This variable seems unused in the context of the prompt
    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=display_barcode_id, # Use display_barcode_id for the form input
                           request_form_data=request_form_data,
                           current_auto_order_id=current_auto_order_id,
                           servo_data=servo_data)

@app.route('/sensor_reset', methods=['GET'])
def sensor_reset():
    global current_item_awaiting_drop
    """
    Endpoint for ESP32 to call when the IR sensor detects an object passing (item dropped).
    This triggers the increment of 'disquantity' and resets the servo states.
    """
    print("[Sensor Trigger] IR detected → Processing disquantity increment and resetting servos.")
    conn = get_db_connection()
    if not conn:
        print("Error: Database connection failed during sensor reset.")
        send_oled_text(["DB CONNECTION", "FAILED!"]) # OLED for DB error on sensor
        session.pop('current_receipt_barcode', None) # Clear session on DB error
        session['item_drop_pending'] = False
        current_item_awaiting_drop = None # Clear global variable
        return "Database connection failed", 500

    try:
        cursor = conn.cursor(dictionary=True) # Changed to dictionary=True
        
        # Call the common logic for disquantity update and state management
        # This function will now handle its own OLED messages for sensor triggers
        _handle_disquantity_logic(
            conn, # Pass connection to helper
            cursor,
            is_sensor_trigger=True
        )

        # Reset all bin values in the database to 0 (servos OFF)
        cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
        conn.commit() # Commit all changes from _handle_disquantity_logic (tbl_order) and tbl_bin.

        # Send OFF commands to ESP32 for all servos directly
        for sid in SERVO_PINS:
            try:
                requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                print(f"[Forced Update] Servo {sid} → OFF")
                servo_cache[sid] = 0  # Update local cache
            except requests.exceptions.RequestException as e:
                print(f"[Error] Cannot control ESP32 for Servo {sid}: {e}")

        return "[OK] Sensor reset success, disquantity updated", 200

    except Exception as e:
        conn.rollback()  # Rollback changes if an error occurs
        print(f"[Error] Sensor reset failed: {e}")
        send_oled_text(["SENSOR RESET", "FAILED!"]) # OLED for general error on sensor
        time.sleep(3)
        send_oled_text(["SCAN RECEIPT", "BARCODE"]) # Return to initial state after error
        session.pop('current_receipt_barcode', None) # Clear session on error
        session['item_drop_pending'] = False
        current_item_awaiting_drop = None # Clear global variable
        return f"[Error] Reset failed: {e}", 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Start the servo control loop in a separate daemon thread
    threading.Thread(target=check_and_control_servo_loop, daemon=True).start()
    # Initial OLED Display message when the Flask app starts
    send_oled_text(["SCAN RECEIPT", "BARCODE"])
    # Run the Flask application
    app.run(debug=True, host='0.0.0.0')
