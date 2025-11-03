from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps
import json

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Define ESP32 IP Address and Port
ESP32_IP = "http://192.168.106.124"
ESP32_PORT = 80

# Define database connection details
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project_bin'
}

# Define Servo GPIO pins on ESP32 (must match ESP32 code)
SERVO_PINS = [12, 13, 14, 26, 27]

# Global cache to store the latest Servo status
servo_cache = {}
last_error_message = None
error_occurrence_count = 0
normal_status_printed = False

def get_db_connection():
    """
    Establishes and returns a database connection.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

def send_oled_text(lines):
    """
    Function to send text to ESP32 for display on the OLED screen.
    Formats the text to 5 lines, each with max 20 characters.
    """
    try:
        # Join lines into a single string with newlines and truncate to 20 chars
        formatted_lines = [line[:20] for line in lines[:5]]
        payload = '\n'.join(formatted_lines)
        
        response = requests.post(f"{ESP32_IP}/oled", data=payload, headers={'Content-Type': 'text/plain'}, timeout=5)
        print(f"OLED display updated: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending data to ESP32's OLED: {e}")

def check_and_control_servo_loop():
    """
    Background loop to check Servo status from the database and control the ESP32.
    """
    global servo_cache, last_error_message, error_occurrence_count, normal_status_printed

    while True:
        try:
            conn = get_db_connection()
            if not conn:
                raise Exception("Failed to get database connection in servo loop.")

            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, value FROM tbl_bin")
            rows = cursor.fetchall()
            conn.close()

            if last_error_message is not None:
                print(f"[Recovered] System is back to normal after error: {last_error_message} ({error_occurrence_count} times)")
                last_error_message = None
                error_occurrence_count = 0
                normal_status_printed = True
            elif not normal_status_printed:
                print("[Status] System is operating normally")
                normal_status_printed = True

            for row in rows:
                servo_id = row["id"]
                value = row["value"]
                action = "on" if value == 1 else "off"

                if servo_id not in servo_cache or servo_cache[servo_id] != value:
                    try:
                        requests.get(f"{ESP32_IP}/servo{servo_id}/{action}", timeout=2)
                        print(f"[Update] Servo {servo_id} -> {action.upper()}")
                        servo_cache[servo_id] = value
                    except requests.exceptions.RequestException as req_e:
                        current_error = f"Cannot control ESP32 for Servo {servo_id} - {req_e}"
                        if current_error != last_error_message:
                            print(f"[Error] {current_error}")
                            last_error_message = current_error
                            error_occurrence_count = 1
                            normal_status_printed = False
                        else:
                            error_occurrence_count += 1
            
        except mysql.connector.Error as db_err:
            current_error = f"Database error: {db_err}"
            if current_error != last_error_message:
                print(f"[Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1

        except Exception as e:
            current_error = f"General error in loop: {e}"
            if current_error != last_error_message:
                print(f"[Loop Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1

        finally:
            time.sleep(0.1)

def role_required(allowed_roles):
    """
    Decorator to restrict web page access based on user role.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'role' not in session:
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
    Turns on the selected Servo and turns off all other Servos.
    """
    if servo_id in SERVO_PINS:
        conn = get_db_connection()
        if not conn:
            flash("Error connecting to the database.", 'danger')
            return redirect(url_for('bin'))

        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE id != %s", (servo_id,))
            cursor.execute("UPDATE tbl_bin SET value = 1 WHERE id = %s", (servo_id,))
            conn.commit()
            flash(f"Successfully turned on Servo {servo_id} and turned off all others.", 'success')
        except Exception as e:
            flash(f"Error toggling Servo status: {e}", 'danger')
            conn.rollback()
        finally:
            conn.close()
    else:
        flash(f"Invalid Servo ID {servo_id}.", 'danger')
    return redirect(url_for('bin'))

@app.route("/", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    """
    Main page for managing bins and displaying data.
    """
    conn = get_db_connection()
    if not conn:
        flash("Error connecting to the database.", 'danger')
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

    if request.method == 'POST':
        request_form_data = request.form.to_dict()
        action = request.form.get('action')
        
        if action == 'add_disquantity':
            barcode_id_filter = request.form.get('barcode_id_for_disquantity', barcode_id_filter)
        elif action == 'search':
            barcode_id_filter = request.form.get('barcode_id_filter_input', barcode_id_filter)
            send_oled_text(["Please scan the", "correct product", "barcode."])
        elif action == 'reset_all_servos':
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

                cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn_reset.commit()
                
            except mysql.connector.Error as err:
                flash(f"Error resetting Servos and increasing discarded quantity: {err}", 'danger')
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))


    if request.method == "POST" and request.form.get('action') == 'add_disquantity':
        barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
        products_id_to_disquantity = request.form.get('products_id_to_disquantity')

        if not barcode_id_to_search or not products_id_to_disquantity:
            flash("Please specify barcode ID and product ID to increment discarded quantity.", 'danger')
            return render_template("bin.html", orders=[], barcode_id_filter=barcode_id_filter, request_form_data=request_form_data, servo_data=servo_data)

        try:
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
                quantity = item_info.get('quantity')
                disquantity = item_info.get('disquantity')
                
                if category_id_for_bin is None:
                    flash(f"No category_id found for product '{product_name}'.", 'danger')
                    conn.close()
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))
                
                session['stored_item_for_increment'] = {
                    'barcode_id': barcode_id_to_search,
                    'products_id': products_id_to_disquantity,
                    'category_id': category_id_for_bin
                }
                
                # Turn on the specific Servo and turn off others
                cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s AND category_id IN (1, 2, 3, 4, 5)", (category_id_for_bin,))
                cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))
                conn.commit()

                # Display product and category on OLED
                lines = [
                    "Product:",
                    f"{product_name}",
                    "Go to bin:",
                    f"{category_id_for_bin}"
                ]
                send_oled_text(lines)

                flash(f"Servo for product '{product_name}' (category_id: {category_id_for_bin}) rotated. Please press 'Reset Servo Status' to confirm.", 'info')
            else:
                flash("No matching product found.", 'danger')
        except mysql.connector.Error as err:
            flash(f"Error during operation: {err}", 'danger')
            conn.rollback()
        finally:
            if conn:
                conn.close()
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))

    # Main logic for displaying order table
    try:
        if barcode_id_filter:
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

            # Display real-time data on the OLED after a successful search
            if orders_data:
                first_order = orders_data[0]
                quantity = first_order['quantity']
                disquantity = first_order['disquantity']
                remaining = quantity - disquantity
                
                lines = [
                    f"Barcode: {barcode_id_filter}",
                    f"Remaining: {remaining}",
                    f"Total: {quantity}",
                    f"Refunded: {disquantity}"
                ]
                send_oled_text(lines)
            else:
                send_oled_text(["No order found", "for this barcode."])
        else:
            orders_data = []
            send_oled_text(["Scan receipt barcode", "to proceed."])
            
    except mysql.connector.Error as err:
        flash(f"Error fetching order data: {err}", 'danger')
    finally:
        if conn:
            conn.close()

    current_auto_order_id = "N/A"
    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=barcode_id_filter,
                           request_form_data=request_form_data,
                           current_auto_order_id=current_auto_order_id,
                           servo_data=servo_data)

@app.route('/sensor_reset', methods=['GET'])
def sensor_reset():
    """
    Endpoint for ESP32 when the IR sensor detects an object passing.
    """
    print("[Sensor Trigger] IR detected -> Processing disquantity increment and resetting servos.")
    conn = get_db_connection()
    if not conn:
        print("Error: Database connection failed during sensor reset.")
        return "Database connection failed", 500

    try:
        cursor = conn.cursor()
        stored_item = session.get('stored_item_for_increment')

        if stored_item:
            barcode_id_to_search = stored_item['barcode_id']
            products_id_to_disquantity = stored_item['products_id']

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
                    cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                   (proposed_disquantity, order_id))
                    print(f"[Disquantity Update] Increased disquantity for item '{product_name}' to {proposed_disquantity}")
                    
                    # Send refund message to display on OLED
                    lines = [f"{product_name}", "Refunded"]
                    send_oled_text(lines)
                    time.sleep(5)
                else:
                    print(f"[Warning] Cannot increase disquantity for '{product_name}'. Reached maximum quantity.")
                    lines = [f"Item '{product_name}'", "exceeds quantity.", "Please contact staff."]
                    send_oled_text(lines)
                    time.sleep(5)

            # Reset the session state after processing
            session.pop('stored_item_for_increment', None)
            
            # Revert to the "scan product barcode" message after 5 seconds
            send_oled_text(["Please scan the", "correct product", "barcode."])
        else:
            print("[Warning] No stored item found in session. Disquantity not incremented.")
            # Send message to display on OLED
            send_oled_text(["Scan receipt barcode", "to proceed."])

        # Reset all bins to value 0
        cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
        conn.commit()

        # Send OFF commands to ESP32 for all servos
        servo_ids_to_reset = [12, 13, 14, 26, 27]
        for sid in servo_ids_to_reset:
            try:
                requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                print(f"[Forced Update] Servo {sid} -> OFF")
                servo_cache[sid] = 0
            except requests.exceptions.RequestException as e:
                print(f"[Error] Cannot control ESP32 for Servo {sid}: {e}")

        conn.commit()
        return "[OK] Sensor reset success, disquantity updated", 200

    except Exception as e:
        conn.rollback()
        print(f"[Error] Sensor reset failed: {e}")
        return f"[Error] Reset failed: {e}", 500
    finally:
        conn.close()

if __name__ == '__main__':
    threading.Thread(target=check_and_control_servo_loop, daemon=True).start()
    app.run(debug=True, host='0.0.0.0')