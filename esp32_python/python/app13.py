from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# ESP32 IP and Port for OLED and Servo control
ESP32_IP = "http://192.168.114.133"
ESP32_PORT = 80

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project_bin'
}

# List of servo pins (IDs) used in the system
SERVO_PINS = [12, 13, 14, 26, 27]
servo_cache = {} # Cache to store the last known state of servos

# Variables for error logging and status messages
last_error_message = None
error_occurrence_count = 0
normal_status_printed = False

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
        payload = '\n'.join(formatted_lines) # Join lines with newline character
        
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
                        servo_cache[servo_id] = value # Update cache
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
            conn.rollback() # Rollback changes if an error occurs
        finally:
            conn.close()
    else:
        flash(f"Invalid Servo ID {servo_id}.", 'danger')
    return redirect(url_for('bin'))

@app.route("/", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    """
    Handles the main bin management page.
    Allows searching for orders by barcode, incrementing discarded quantities,
    updating tbl_bin for servo control, and displaying current servo states.
    Also manages OLED display messages based on user actions.
    """
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", 'danger')
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

    if request.method == 'POST':
        request_form_data = request.form.to_dict()
        action = request.form.get('action')
        
        if action == 'add_disquantity':
            # Action to prepare for incrementing discarded quantity
            barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
            products_id_to_disquantity = request.form.get('products_id_to_disquantity')
            
            if not barcode_id_to_search or not products_id_to_disquantity:
                flash("Please specify barcode ID and product ID to increment discarded quantity.", 'danger')
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
                        conn.close()
                        return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))
                    
                    # Store item details in session for later use by sensor_reset
                    session['stored_item_for_increment'] = {
                        'barcode_id': barcode_id_to_search,
                        'products_id': products_id_to_disquantity,
                        'category_id': category_id_for_bin
                    }
                    
                    # Update tbl_bin to open the correct bin (category_id)
                    cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s AND category_id IN (1, 2, 3, 4, 5)", (category_id_for_bin,))
                    cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))
                    conn.commit()

                    # *** OLED Display: Product name and category for dropping ***
                    lines = [
                        f"PRODUCT: {product_name}", # Ensure product_name is short
                        f"DROP IN BIN: {category_id_for_bin}"
                    ]
                    send_oled_text(lines)
                else:
                    flash("No matching product found.", 'danger')
            except mysql.connector.Error as err:
                flash(f"Error during operation: {err}", 'danger')
                conn.rollback()
            finally:
                if conn:
                    conn.close()
                return redirect(url_for('bin', barcode_id_filter=barcode_id_to_search))

        elif request.form.get('action') == 'search':
            # Action when user searches for a barcode
            barcode_id_filter = request.form.get('barcode_id_filter_input', barcode_id_filter)
            if barcode_id_filter:
                # *** OLED Display: Prompt to scan product barcode after a receipt barcode is searched ***
                send_oled_text(["Please scan a valid", "product barcode."])
        elif request.form.get('action') == 'reset_all_servos':
            # Action to manually reset all servos and potentially increment disquantity
            conn_reset = get_db_connection()
            if not conn_reset:
                flash("Error connecting to the database to reset Servos.", 'danger')
                return redirect(url_for('bin'))
            try:
                cursor_reset = conn_reset.cursor()
                stored_item = session.get('stored_item_for_increment')

                if stored_item:
                    barcode_id_to_search = stored_item['barcode_id']
                    products_id_to_disquantity = stored_item['products_id']
                    
                    cursor_reset.execute("""
                        SELECT o.id, o.quantity, o.disquantity, o.products_name, o.products_id
                        FROM tbl_order o
                        JOIN tbl_products p ON o.products_id = p.products_id
                        WHERE o.barcode_id = %s AND o.products_id = %s
                    """, (barcode_id_to_search, products_id_to_disquantity))
                    order_item_to_update = cursor_reset.fetchone()

                    if order_item_to_update:
                        current_quantity = order_item_to_update[1]
                        current_disquantity = order_item_to_update[2]
                        product_name = order_item_to_update[3]

                        proposed_disquantity = current_disquantity + 1

                        if proposed_disquantity <= current_quantity:
                            cursor_reset.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                                 (proposed_disquantity, order_item_to_update[0]))
                            flash(f"Successfully increased discarded quantity for '{product_name}'.", 'success')
                        else:
                            flash(f"Cannot increase discarded quantity beyond existing product quantity.", 'danger')
                    else:
                        flash("No matching product found.", 'danger')
                else:
                    flash("No product selected for incrementing discarded quantity. Only resetting Servo status.", 'info')

                # Reset all bin values to 0 (servos OFF)
                cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn_reset.commit()
                
            except mysql.connector.Error as err:
                flash(f"Error resetting Servos and increasing discarded quantity: {err}", 'danger')
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))


    # Main logic for displaying order table based on barcode_id_filter
    try:
        if barcode_id_filter:
            # Query to get all orders with the same barcode_id
            base_query = """
                SELECT o.*, p.price, p.products_name, p.category_id
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s
            """
            query_params = [barcode_id_filter]
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
            total_query_params = [barcode_id_filter]
            cursor.execute(total_query, tuple(total_query_params))
            totals = cursor.fetchone()

            if totals and totals['total_quantity'] is not None:
                total_quantity = totals['total_quantity']
                total_disquantity = totals['total_disquantity']
                remaining = total_quantity - total_disquantity
                
                # *** OLED Display: Barcode summary ***
                lines = [
                    f"BARCODE: {barcode_id_filter}",
                    f"REMAINING: {remaining}",
                    f"TOTAL: {total_quantity}",
                    f"REFUNDED: {total_disquantity}"
                ]
                send_oled_text(lines)
            else:
                # *** OLED Display: No order found for barcode ***
                send_oled_text(["No order found", "for this barcode."])
        else:
            orders_data = []
            # *** OLED Display: Initial prompt for scanning receipt barcode ***
            send_oled_text(["Please scan a valid", "product barcode."])
            
    except mysql.connector.Error as err:
        flash(f"Error fetching order data: {err}", 'danger')
    finally:
        if conn:
            conn.close()

    current_auto_order_id = "N/A" # This variable seems unused in the context of the prompt
    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=barcode_id_filter,
                           request_form_data=request_form_data,
                           current_auto_order_id=current_auto_order_id,
                           servo_data=servo_data)

@app.route('/sensor_reset', methods=['GET'])
def sensor_reset():
    """
    Endpoint for ESP32 to call when the IR sensor detects an object passing (item dropped).
    This triggers the increment of 'disquantity' and resets the servo states.
    """
    print("[Sensor Trigger] IR detected -> Processing disquantity increment and resetting servos.")
    conn = get_db_connection()
    if not conn:
        print("Error: Database connection failed during sensor reset.")
        return "Database connection failed", 500

    try:
        cursor = conn.cursor()
        stored_item = session.get('stored_item_for_increment') # Retrieve stored item from session

        if stored_item:
            barcode_id_to_search = stored_item['barcode_id']
            products_id_to_disquantity = stored_item['products_id']

            # Fetch the order item to update its disquantity
            cursor.execute("""
                SELECT id, quantity, disquantity, products_name
                FROM tbl_order
                WHERE barcode_id = %s AND products_id = %s
            """, (barcode_id_to_search, products_id_to_disquantity))
            order_item_to_update = cursor.fetchone()

            if order_item_to_update:
                order_id = order_item_to_update[0]
                current_quantity = order_item_to_update[1]
                current_disquantity = order_item_to_update[2]
                product_name = order_item_to_update[3]
                proposed_disquantity = current_disquantity + 1

                if proposed_disquantity <= current_quantity:
                    # Increment disquantity in the database
                    cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                   (proposed_disquantity, order_id))
                    print(f"[Disquantity Update] Increased disquantity for item '{product_name}' to {proposed_disquantity}")
                    
                    # *** OLED Display: Product name and "Refunded" ***
                    lines = [f"{product_name}", "REFUNDED"]
                    send_oled_text(lines)
                    time.sleep(5) # Display for 5 seconds
                else:
                    print(f"[Warning] Cannot increase disquantity for '{product_name}'. Reached maximum quantity.")
                    # *** OLED Display: Warning if quantity exceeds ***
                    lines = [f"ITEM '{product_name}'", "EXCEEDS QUANTITY.", "PLEASE CONTACT STAFF."]
                    send_oled_text(lines)
                    time.sleep(5) # Display for 5 seconds

            session.pop('stored_item_for_increment', None) # Clear the stored item from session

            # *** OLED Display: Revert to initial "scan product barcode" message ***
            send_oled_text(["Please scan a valid", "product barcode."])
        else:
            print("[Warning] No stored item found in session. Disquantity not incremented.")
            # *** OLED Display: If no item was stored, revert to initial receipt barcode prompt ***
            send_oled_text(["Please scan a valid", "product barcode."])

        # Reset all bin values in the database to 0 (servos OFF)
        cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
        conn.commit()

        # Send OFF commands to ESP32 for all servos directly
        servo_ids_to_reset = [12, 13, 14, 26, 27]
        for sid in servo_ids_to_reset:
            try:
                requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                print(f"[Forced Update] Servo {sid} -> OFF")
                servo_cache[sid] = 0 # Update local cache
            except requests.exceptions.RequestException as e:
                print(f"[Error] Cannot control ESP32 for Servo {sid}: {e}")

        conn.commit()
        return "[OK] Sensor reset success, disquantity updated", 200

    except Exception as e:
        conn.rollback() # Rollback changes if an error occurs
        print(f"[Error] Sensor reset failed: {e}")
        return f"[Error] Reset failed: {e}", 500
    finally:
        conn.close()

if __name__ == '__main__':
    # Start the servo control loop in a separate daemon thread
    threading.Thread(target=check_and_control_servo_loop, daemon=True).start()
    # *** Initial OLED Display message when the Flask app starts ***
    send_oled_text(["Please scan a valid", "product barcode."])
    # Run the Flask application
    app.run(debug=True, host='0.0.0.0')
