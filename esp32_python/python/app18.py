from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' # Set Secret Key for Flask Session

# IP and Port of ESP32 for controlling OLED and Servo
ESP32_IP = "http://192.168.147.134"
ESP32_PORT = 80

# --- NEW: Define the store ID for this specific machine ---
# กำหนด Store ID ของเครื่องทิ้งขยะเครื่องนี้
# บาร์โค้ดที่มาจาก Store ID อื่นจะไม่สามารถใช้งานได้
CURRENT_MACHINE_STORE_ID = 1

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project_bin'
}

# List of Servo pins (IDs) used in the system
SERVO_PINS = [12, 13, 14, 26, 27]
servo_cache = {}  # Cache to store the latest status of Servos

# Variables for logging errors and status messages
last_error_message = None
error_occurrence_count = 0
normal_status_printed = False

# Global variable to store items awaiting disposal (replaces session for sensor calls)
# Assuming single-user/single-device usage, with one item "ready to drop" at a time
current_item_awaiting_drop = None

def get_db_connection():
    """
    Establishes a database connection using DB_CONFIG.
    Returns a connection object, or None if an error occurs.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

def send_oled_text(lines):
    """
    Sends text to ESP32 to display on the OLED screen.
    Formats the text to a maximum of 5 lines, each line up to 20 characters.
    Each item in the 'lines' list will be displayed on a separate line automatically.
    """
    try:
        # Trim each line to 20 characters and use a maximum of 5 lines
        formatted_lines = [line[:20] for line in lines[:5]]
        # Join lines with 'n' as a delimiter, which will display them on separate OLED lines
        payload = '\n'.join(formatted_lines)
        # Send data to ESP32 at the /oled endpoint
        response = requests.post(f"{ESP32_IP}/oled", data=payload, headers={'Content-Type': 'text/plain'}, timeout=5)
        print(f"OLED display updated: {response.text}")
    except requests.exceptions.RequestException as e:
        # Log any errors that occur while sending the request to ESP32
        print(f"Error sending data to ESP32's OLED: {e}")

def check_and_control_servo_loop():
    """
    Continuously checks Servo status from the database and sends control commands to ESP32.
    Handles database and network errors, and logs detailed error and system recovery messages.
    This function runs in a separate thread.
    """
    global servo_cache
    global last_error_message, error_occurrence_count, normal_status_printed
    while True:
        try:
            conn = get_db_connection()
            if not conn:
                # If database connection fails, raise an Exception to be caught
                raise Exception("Failed to get database connection in servo loop.")
            cursor = conn.cursor(dictionary=True)
            # Retrieve current Servo status from tbl_bin
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
            # Iterate through retrieved Servo statuses and control ESP32 if status has changed
            for row in rows:
                servo_id = row["id"]
                value = row["value"]
                action = "on" if value == 1 else "off"
                if servo_id not in servo_cache or servo_cache[servo_id] != value:
                    try:
                        # Send command to ESP32 to control Servo
                        requests.get(f"{ESP32_IP}/servo{servo_id}/{action}", timeout=2)
                        print(f"[Update] Servo {servo_id} → {action.upper()}")
                        servo_cache[servo_id] = value  # Update cache
                    except requests.exceptions.RequestException as req_e:
                        # Handle communication errors with ESP32
                        current_error = f"Cannot control ESP32 for Servo {servo_id} - {req_e}"
                        if current_error != last_error_message:
                            print(f"[Error] {current_error}")
                            last_error_message = current_error
                            error_occurrence_count = 1
                            normal_status_printed = False
                        else:
                            error_occurrence_count += 1
        except mysql.connector.Error as db_err:
            # Handle database-related errors
            current_error = f"Database error: {db_err}"
            if current_error != last_error_message:
                print(f"[Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1
        except Exception as e:
            # Handle other unexpected errors in the loop
            current_error = f"General error in loop: {e}"
            if current_error != last_error_message:
                print(f"[Loop Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1
        finally:
            # Short delay before the next iteration
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
        is_sensor_trigger (bool): True if called by a sensor, False if called by a button.
        flash_func (callable): Flask's flash function (for button only, pass None for sensor).
    """

    item_to_process = None
    if is_sensor_trigger:
        item_to_process = current_item_awaiting_drop
        # Do not clear the global variable immediately, it will be cleared after checking remaining items.
    else: # Button press, use session
        item_to_process = session.get('stored_item_for_increment')
        session.pop('stored_item_for_increment', None) # Clear session for button

    if item_to_process:
        barcode_id_to_search = item_to_process['barcode_id']
        products_id_to_disquantity = item_to_process['products_id']
        product_name = item_to_process.get('products_name', 'UNKNOWN ITEM') # Get product name from stored item

        # Fetch order item to update disquantity
        cursor.execute("""
            SELECT id, quantity, disquantity, products_name
            FROM tbl_order
            WHERE barcode_id = %s AND products_id = %s
        """, (barcode_id_to_search, products_id_to_disquantity))
        order_item_to_update = cursor.fetchone()

        if order_item_to_update:
            # Access dictionary keys directly
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
                    
                    # *** OLED display for successful refund (2 lines) ***
                    send_oled_text([
                        f"{product_name[:20]}", # Line 1: Product name (trimmed to 20 chars)
                        "Refunded!"             # Line 2: Status message
                    ])
                    time.sleep(2) # Display message for 2 seconds

                    # Fetch new totals for the barcode to get current status
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

                        # *** OLED Display: Barcode Summary (4 neatly spaced lines) ***
                        # CORRECTED: Each piece of information now on its own line
                        send_oled_text([
                            f"Barcode:{barcode_id_to_search[:12]}", # Line 1: Barcode ID
                            f"Rem:{remaining}",                     # Line 2: Remaining quantity
                            f"Total:{total_quantity}",              # Line 3: Total quantity
                            f"Refunded:{total_disquantity}"         # Line 4: Refunded quantity
                        ])
                        print(f"[OLED Update] Displaying barcode summary for {barcode_id_to_search} after refund. Remaining: {remaining}")

                        # Clear current_item_awaiting_drop (this item has been processed)
                        current_item_awaiting_drop = None
                        session['item_drop_pending'] = False # Set status ready for next selection/scan

                        # If all items for this receipt are refunded, clear receipt barcode from session
                        if remaining == 0:
                            session.pop('current_receipt_barcode', None)
                            print(f"[OLED Update] All items for barcode {barcode_id_to_search} refunded. Receipt barcode cleared.")
                    else:
                        # Fallback case if totals cannot be fetched after refund
                        # *** OLED Display: Error and prompt (2 lines) ***
                        send_oled_text(["Error:No totals", "Scan Receipt"])
                        current_item_awaiting_drop = None
                        session.pop('current_receipt_barcode', None)
                        session['item_drop_pending'] = False
                        print("[OLED Update] Could not fetch totals after refund, reverting to scan receipt state.")
                else: # Button press (usually 'reset_all_servos' action which includes disquantity logic)
                    flash_func(f"Disquantity for '{product_name}' incremented successfully.", 'success')
            else: # Proposed disquantity exceeds current quantity
                if is_sensor_trigger:
                    print(f"[Warning] Cannot increase disquantity for '{product_name}'. Reached maximum quantity.")
                    # *** OLED display for quantity exceeded warning (3 lines) ***
                    send_oled_text([
                        f"Item '{product_name[:14]}'", # Line 1: Product name
                        "Qty Exceeded.",               # Line 2: Quantity exceeded warning
                        "Contact staff."               # Line 3: Instruction
                    ])
                    time.sleep(5) # Display warning for 5 seconds
                    # *** OLED Display: Return to initial state (2 lines) ***
                    send_oled_text(["Scan Receipt", "Barcode"]) # Return to initial state
                    current_item_awaiting_drop = None # Clear global variable if error occurs
                    session.pop('current_receipt_barcode', None)
                    session['item_drop_pending'] = False
                else: # Button press
                    flash_func(f"Cannot increase disquantity beyond available quantity for '{product_name}'", 'danger')
        else: # No matching item found for update
            if is_sensor_trigger:
                print("[Warning] No matching product found for disquantity increment (sensor).")
                # *** OLED display for no active item (2 lines) ***
                send_oled_text(["No Active Item", "Scan Receipt"])
                time.sleep(3) # Display for 3 seconds
                # *** OLED Display: Return to initial state (2 lines) ***
                send_oled_text(["Scan Receipt", "Barcode"]) # Return to initial state
                current_item_awaiting_drop = None # Clear global variable if no active item
                session.pop('current_receipt_barcode', None)
                session['item_drop_pending'] = False
            else: # Button press
                flash_func("No matching product found for disquantity increment", 'danger')
    else: # No active item
        if is_sensor_trigger:
            print("[Warning] No active item awaiting drop (sensor). Disquantity not incremented.")
            # *** OLED display for no active item (2 lines) ***
            send_oled_text(["No Active Item", "Scan Receipt"])
            time.sleep(3) # Display for 3 seconds
            # *** OLED Display: Return to initial state (2 lines) ***
            send_oled_text(["Scan Receipt", "Barcode"]) # Return to initial state
            current_item_awaiting_drop = None # Clear global variable if no active item
            session.pop('current_receipt_barcode', None)
            session['item_drop_pending'] = False
        else: # Button press
            flash_func("No item selected for disquantity increment. Servos reset only.", 'info')


@app.route('/toggle_servo/<int:servo_id>', methods=['POST'])
def toggle_servo(servo_id):
    """
    Toggles the status of a specific Servo in the database (sets it to 1 and others to 0).
    This endpoint is usually called from the web UI.
    """
    if servo_id in SERVO_PINS:
        conn = get_db_connection()
        if not conn:
            flash("Error connecting to the database.", 'danger')
            return redirect(url_for('bin'))
        try:
            cursor = conn.cursor(dictionary=True)
            # Set all other Servos to OFF (value = 0)
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE id != %s", (servo_id,))
            # Set the selected Servo to ON (value = 1)
            cursor.execute("UPDATE tbl_bin SET value = 1 WHERE id = %s", (servo_id,))
            conn.commit()
            flash(f"Servo {servo_id} turned ON, and all other Servos turned OFF successfully.", 'success')
        except Exception as e:
            flash(f"Error toggling Servo status: {e}", 'danger')
            conn.rollback()  # Rollback changes if an error occurs
        finally:
            conn.close()
    else:
        flash(f"Invalid Servo ID {servo_id}", 'danger')
    return redirect(url_for('bin'))

@app.route("/", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    global current_item_awaiting_drop
    """
    Manages the main bin management page.
    Allows searching for order items by barcode, incrementing disquantity,
    updating tbl_bin for Servo control, and displaying current Servo status.
    Also manages OLED display messages based on user actions.
    """

    # This block handles POST requests from the form
    if request.method == 'POST':
        request_form_data = request.form.to_dict()
        action = request.form.get('action')
        
        if action == 'add_disquantity':
            barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
            products_id_to_disquantity = request.form.get('products_id_to_disquantity')
            
            if not barcode_id_to_search or not products_id_to_disquantity:
                flash("Please provide Barcode ID and Product ID to increment disquantity.", 'danger')
                send_oled_text(["Missing Data", "Scan Receipt"])
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search or ''))

            conn = get_db_connection()
            if not conn:
                flash("Database connection error.", 'danger')
                send_oled_text(["DB Disconnected", "Error!"])
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))

            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute("""
                    SELECT p.category_id, o.products_name, o.quantity, o.disquantity
                    FROM tbl_order o
                    JOIN tbl_products p ON o.products_id = p.products_id
                    WHERE o.barcode_id = %s AND o.products_id = %s
                """, (barcode_id_to_search, products_id_to_disquantity))
                item_info = cursor.fetchone()

                if not item_info:
                    flash("No matching product found.", 'danger')
                    send_oled_text(["No Product", "Found"])
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))

                category_id_for_bin = item_info.get('category_id')
                product_name = item_info.get('products_name')
                current_quantity = item_info.get('quantity')
                current_disquantity = item_info.get('disquantity')

                if category_id_for_bin is None:
                    flash(f"No category_id found for product '{product_name}'", 'danger')
                    send_oled_text(["No Category", "Contact Staff"])
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
                
                # *** FIX STARTS HERE ***
                # Check if the quantity is already maxed out
                if current_disquantity >= current_quantity:
                    flash(f"Cannot increase disquantity for '{product_name}'. All items for this product have already been discarded.", 'danger')
                    send_oled_text([f"Item '{product_name[:14]}'", "Qty Exceeded.", "Contact staff."])
                    print(f"[Warning] Cannot add disquantity for '{product_name}'. Already at max quantity. Resetting state.")
                    
                    # Explicitly reset state when this error occurs to prevent getting stuck
                    session.pop('stored_item_for_increment', None)
                    session['item_drop_pending'] = False
                    current_item_awaiting_drop = None
                    cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                    conn.commit()
                    
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
                # *** FIX ENDS HERE ***

                # If all checks pass, proceed to open the bin
                session['stored_item_for_increment'] = {'barcode_id': barcode_id_to_search, 'products_id': products_id_to_disquantity, 'category_id': category_id_for_bin}
                current_item_awaiting_drop = {'barcode_id': barcode_id_to_search, 'products_id': products_id_to_disquantity, 'category_id': category_id_for_bin, 'products_name': product_name}
                session['current_receipt_barcode'] = barcode_id_to_search
                session['item_drop_pending'] = True

                cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s AND category_id IN (1, 2, 3, 4, 5)", (category_id_for_bin,))
                cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))
                conn.commit()
                
                send_oled_text([f"Item: {products_id_to_disquantity[:14]}", f"Drop in Bin: {category_id_for_bin}"])
                
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))

            except mysql.connector.Error as err:
                flash(f"Error during operation: {err}", 'danger')
                if conn: conn.rollback()
                send_oled_text(["DB Error", "Check Log"])
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
            finally:
                if conn and conn.is_connected():
                    conn.close()
        
        elif action == 'reset_all_servos':
            barcode_id_filter_to_preserve = request.form.get('barcode_id_filter', '')
            conn_reset = get_db_connection()
            if not conn_reset:
                flash("Error connecting to the database to reset Servos.", 'danger')
                send_oled_text(["DB Disconnected", "Error!"])
                return redirect(url_for('bin', barcode_id_filter=barcode_id_filter_to_preserve))
            try:
                cursor_reset = conn_reset.cursor(dictionary=True)
                _handle_disquantity_logic(conn_reset, cursor_reset, is_sensor_trigger=False, flash_func=flash)

                cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn_reset.commit()

                for sid in SERVO_PINS:
                    try:
                        requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                        print(f"[Forced Update - Button] Servo {sid} → OFF")
                        servo_cache[sid] = 0
                    except requests.exceptions.RequestException as e:
                        print(f"[Error - Button] Cannot control ESP32 for Servo {sid}: {e}")
                
                send_oled_text(["System Reset.", "Ready for scan."])
                time.sleep(2)
                send_oled_text(["Scan Receipt", "Barcode"])
                session.pop('current_receipt_barcode', None)
                session.pop('stored_item_for_increment', None)
                session['item_drop_pending'] = False
                current_item_awaiting_drop = None

            except mysql.connector.Error as err:
                flash(f"Error resetting Servos and increasing discarded quantity: {err}", 'danger')
                send_oled_text(["DB Error", "Reset Failed!"])
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
                return redirect(url_for('bin', barcode_id_filter=barcode_id_filter_to_preserve))

    # This block handles GET requests (initial page load and search resets)
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", 'danger')
        send_oled_text(["DB Disconnected", "Error!"])
        return render_template("bin.html", orders=[], barcode_id_filter='', request_form_data={}, servo_data={})
    
    cursor = conn.cursor(dictionary=True)
    orders_data = []
    barcode_id_filter = request.args.get('barcode_id_filter', '')
    request_form_data = {}
    servo_data = {}
    
    try:
        cursor.execute("SELECT id, value FROM tbl_bin")
        servo_rows = cursor.fetchall()
        servo_data = {row['id']: row['value'] for row in servo_rows}
    except mysql.connector.Error as err:
        flash(f"Error fetching Servo data: {err}", 'danger')
        send_oled_text(["DB Error", "Servo!"])

    action = request.args.get('action')
    if action == 'reset_search':
        print("[Reset Search Action] Clearing search filter, resetting servos, and OLED.")
        barcode_id_filter = ''
        try:
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
            conn.commit()
            for sid in SERVO_PINS:
                try:
                    requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                    print(f"[Forced Update - Reset Search] Servo {sid} → OFF")
                    servo_cache[sid] = 0
                except requests.exceptions.RequestException as e:
                    print(f"[Error - Reset Search] Cannot control ESP32 for Servo {sid}: {e}")
            
            send_oled_text(["Scan Receipt", "Barcode"])
            session.pop('current_receipt_barcode', None)
            session.pop('stored_item_for_increment', None)
            session['item_drop_pending'] = False
            current_item_awaiting_drop = None
            flash("Search reset and all Servos turned OFF.", 'info')
        except mysql.connector.Error as err:
            flash(f"Error resetting Servos during search reset: {err}", 'danger')
            send_oled_text(["DB Error", "Reset Failed!"])
            conn.rollback()
        finally:
            if conn: conn.close()
            return redirect(url_for('bin', barcode_id_filter=''))
    
    display_barcode_id = barcode_id_filter
    if not display_barcode_id and session.get('current_receipt_barcode'):
        display_barcode_id = session.get('current_receipt_barcode')

    if display_barcode_id:
        # --- MODIFIED BLOCK: Validate barcode's store_id before fetching items ---
        try:
            # NOTE: This assumes 'tbl_order' has a 'store_id' column.
            # หมายเหตุ: โค้ดส่วนนี้ตั้งสมมติฐานว่าตาราง 'tbl_order' มีคอลัมน์ 'store_id'
            cursor.execute("SELECT store_id FROM tbl_order WHERE barcode_id = %s LIMIT 1", (display_barcode_id,))
            order_info = cursor.fetchone()

            if not order_info:
                flash(f"ไม่พบบาร์โค้ด '{display_barcode_id}' ในระบบ", 'warning')
                send_oled_text(["Barcode Not Found", "Scan New Receipt"])
                session.pop('current_receipt_barcode', None)
                return redirect(url_for('bin'))

            # Check if the store_id from the order matches this machine's store_id
            # ตรวจสอบว่า store_id จากใบเสร็จตรงกับ store_id ของเครื่องนี้หรือไม่
            order_store_id = order_info.get('store_id')
            if order_store_id != CURRENT_MACHINE_STORE_ID:
                flash(f"ใบเสร็จ '{display_barcode_id}' ไม่ใช่สำหรับสาขานี้", 'danger')
                send_oled_text(["Wrong Store", "Scan Again"])
                session.pop('current_receipt_barcode', None)
                return redirect(url_for('bin'))

            # If store_id is correct, proceed to fetch all items for the barcode
            # ถ้า store_id ถูกต้อง ให้ดึงข้อมูลรายการสินค้าทั้งหมดสำหรับบาร์โค้ดนั้น
            base_query = "SELECT o.*, p.price, p.products_name, p.category_id FROM tbl_order o JOIN tbl_products p ON o.products_id = p.products_id WHERE o.barcode_id = %s"
            query_params = [display_barcode_id]
            if session.get('role') == 'member':
                base_query += " AND o.email = %s"
                query_params.append(session['email'])
            base_query += " ORDER BY o.id DESC"
            cursor.execute(base_query, tuple(query_params))
            orders_data = cursor.fetchall()

            total_query = "SELECT SUM(quantity) AS total_quantity, SUM(disquantity) AS total_disquantity FROM tbl_order WHERE barcode_id = %s"
            cursor.execute(total_query, (display_barcode_id,))
            totals = cursor.fetchone()

            if orders_data and totals and totals['total_quantity'] is not None:
                if not session.get('item_drop_pending'):
                    total_quantity = totals['total_quantity']
                    total_disquantity = totals['total_disquantity']
                    remaining = total_quantity - total_disquantity
                    send_oled_text([
                        f"Barcode:{display_barcode_id[:12]}",
                        f"Rem:{remaining}",
                        f"Total:{total_quantity}",
                        f"Refunded:{total_disquantity}"
                    ])
            else:
                send_oled_text(["No Orders Found", "Scan New Receipt"])
                session.pop('current_receipt_barcode', None)
                session['item_drop_pending'] = False
                current_item_awaiting_drop = None
        except mysql.connector.Error as err:
            flash(f"Database error during barcode validation: {err}", 'danger')
            send_oled_text(["DB Error", "Validation Failed"])
            return redirect(url_for('bin'))
    else:
        if not session.get('item_drop_pending'):
            send_oled_text(["Scan Receipt", "Barcode"])
            session.pop('current_receipt_barcode', None)
            session['item_drop_pending'] = False
            current_item_awaiting_drop = None
    
    if conn.is_connected():
        conn.close()

    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=display_barcode_id,
                           request_form_data=request_form_data,
                           servo_data=servo_data)

@app.route('/sensor_reset', methods=['GET'])
def sensor_reset():
    global current_item_awaiting_drop
    """
    Endpoint for ESP32 to call when an IR sensor detects an object passing (item discarded).
    This triggers the 'disquantity' increment and resets Servo status.
    """
    print("[Sensor Trigger] IR detected → Processing disquantity increment and resetting servos.")
    conn = get_db_connection()
    if not conn:
        print("Error: Database connection failed during sensor reset.")
        # *** OLED for DB error in sensor (2 lines) ***
        send_oled_text(["DB Disconnected", "Error!"])
        session.pop('current_receipt_barcode', None) # Clear session on DB error
        session['item_drop_pending'] = False
        current_item_awaiting_drop = None # Clear global variable
        return "Database connection failed", 500

    try:
        cursor = conn.cursor(dictionary=True) # Change to dictionary=True
        
        # Call the shared logic for disquantity update and status management
        # This function will handle its own OLED messages for sensor calls
        _handle_disquantity_logic(
            conn, # Pass connection to helper
            cursor,
            is_sensor_trigger=True
        )

        # Reset all bin values in the database to 0 (Servos OFF)
        cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
        conn.commit() # Commit all changes from _handle_disquantity_logic (tbl_order) and tbl_bin

        # Send OFF commands to all Servos on ESP32 directly
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
        # *** OLED for general error in sensor (2 lines) ***
        send_oled_text(["Sensor Reset", "Failed!"])
        time.sleep(3)
        # *** OLED Display: Return to initial state after error (2 lines) ***
        send_oled_text(["Scan Receipt", "Barcode"]) # Return to initial state after error
        session.pop('current_receipt_barcode', None) # Clear session on error
        session['item_drop_pending'] = False
        current_item_awaiting_drop = None # Clear global variable
        return f"[Error] Reset failed: {e}", 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Start the servo control loop in a separate daemon thread
    threading.Thread(target=check_and_control_servo_loop, daemon=True).start()
    # *** Initial OLED display message when Flask app starts (2 lines) ***
    send_oled_text(["Scan Receipt", "Barcode"])
    # Run Flask application
    app.run(host="0.0.0.0", port=5000, debug=False)

