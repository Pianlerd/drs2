from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps # Import wraps for decorator

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' # ตั้งค่า secret key สำหรับ session และ flash messages

ESP32_IP = "http://192.168.50.124"

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project_bin'
}

LED_PINS = [12, 13, 14, 26, 27]
led_cache = {}

# --- เพิ่มตัวแปรสำหรับจัดการ Error Output ---
last_error_message = None
error_occurrence_count = 0
normal_status_printed = False # ใช้ติดตามว่าสถานะปกติถูกพิมพ์ไปแล้วหรือยัง
# --- สิ้นสุดตัวแปรจัดการ Error Output ---

def get_db_connection():
    """
    Establish a database connection.
    """
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

def check_and_control_led_loop():
    """
    Continuously check LED states from the database and control ESP32.
    Handles database and network errors, providing logging.
    """
    global led_cache
    global last_error_message, error_occurrence_count, normal_status_printed

    while True:
        try:
            # --- Normal operation (no error) ---
            conn = get_db_connection()
            if not conn:
                raise Exception("Failed to get database connection in LED loop.")

            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, value FROM tbl_bin")
            rows = cursor.fetchall()
            conn.close()

            # If no error now and there was an error before, notify recovery
            if last_error_message is not None:
                print(f"[Recovered] System is back to normal after error: {last_error_message} ({error_occurrence_count} times)")
                last_error_message = None
                error_occurrence_count = 0
                normal_status_printed = True # Set that normal status has been printed

            # If normal status and not yet printed, print notification
            elif not normal_status_printed:
                print("[Status] System is operating normally")
                normal_status_printed = True


            for row in rows:
                led_id = row["id"]
                value = row["value"]
                action = "on" if value == 1 else "off"

                if led_id not in led_cache or led_cache[led_id] != value:
                    led_cache[led_id] = value
                    try:
                        requests.get(f"{ESP32_IP}/led{led_id}/{action}", timeout=2)
                        print(f"[Update] LED {led_id} → {action.upper()}")
                    except requests.exceptions.RequestException as req_e:
                        # Separate ESP32 connection error
                        current_error = f"Cannot control ESP32 for LED {led_id} - {req_e}"
                        if current_error != last_error_message:
                            # New error
                            print(f"[Error] {current_error}")
                            last_error_message = current_error
                            error_occurrence_count = 1
                            normal_status_printed = False # Error occurred, not normal status
                        else:
                            # Same error, increment count
                            error_occurrence_count += 1
                            # Do not print repeatedly
            
        except mysql.connector.Error as db_err:
            # Separate database connection error
            current_error = f"Database error: {db_err}"
            if current_error != last_error_message:
                # New error
                print(f"[Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                # Same error, increment count
                error_occurrence_count += 1

        except Exception as e:
            # Catch All Other Errors
            current_error = f"General error in loop: {e}"
            if current_error != last_error_message:
                print(f"[Loop Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1

        finally:
            time.sleep(0.1) # Check every 100 milliseconds

# --- Placeholder for role_required decorator ---
def role_required(allowed_roles):
    """
    Decorator to restrict access based on user roles.
    For demonstration, this placeholder always allows access.
    In a real application, you would implement actual role checking
    (e.g., from session or a user management system).
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # --- Placeholder for actual role checking logic ---
            if 'role' not in session:
                session['role'] = 'root_admin' # Default role for testing if not logged in
                session['email'] = 'test@example.com' # Default email for testing

            user_role = session.get('role')
            if user_role not in allowed_roles:
                flash("คุณไม่มีสิทธิ์เข้าถึงหน้านี้.", 'danger')
                return redirect(url_for('bin')) # Redirect to bin page if unauthorized
            # --- End placeholder ---
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/toggle/<int:led_id>', methods=['POST'])
def toggle_led(led_id):
    """
    Toggle the state of a specific LED and update the database.
    This function is now called from the /bin page.
    Modified: When an LED is selected, it will be turned ON, and all other LEDs will be turned OFF.
    """
    if led_id in LED_PINS:
        conn = get_db_connection()
        if not conn:
            flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
            return redirect(url_for('bin'))

        try:
            cursor = conn.cursor(dictionary=True) # ใช้ cursor แบบ dictionary เพื่อความสอดคล้องกับโค้ดเดิม

            # ขั้นตอนที่ 1: ตั้งค่า LED อื่นๆ ทั้งหมดเป็น OFF (value = 0)
            # เพื่อให้แน่ใจว่ามี LED เพียงตัวเดียวเท่านั้นที่เปิดอยู่
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE id != %s", (led_id,))

            # ขั้นตอนที่ 2: ตั้งค่า LED ที่ถูกเลือกเป็น ON (value = 1)
            # ตามคำขอของผู้ใช้ LED ที่ถูกเลือกควรจะเปิดเสมอ
            cursor.execute("UPDATE tbl_bin SET value = 1 WHERE id = %s", (led_id,))
            conn.commit()
            flash(f"เปิด LED {led_id} และปิด LED อื่นๆ ทั้งหมดสำเร็จ.", 'success')
        except Exception as e:
            flash(f"เกิดข้อผิดพลาดในการสลับสถานะ LED: {e}", 'danger')
            conn.rollback()
        finally:
            conn.close()
    else:
        flash(f"LED ID {led_id} ไม่ถูกต้อง.", 'danger')
    return redirect(url_for('bin')) # เปลี่ยนเส้นทางกลับหน้า bin เสมอ

@app.route("/", methods=["GET", "POST"]) # Changed route to '/'
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    """
    Handle the bin management page, including searching orders and
    incrementing 'disquantity' for items, updating tbl_bin,
    and displaying LED control. This is now the default homepage.
    """
    conn = get_db_connection()
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("bin.html", orders=[], barcode_id_filter='', request_form_data={}, led_data={})

    cursor = conn.cursor(dictionary=True)
    orders_data = [] # Data for order items to display
    barcode_id_filter = request.args.get('barcode_id_filter', '') # For GET requests (search/reset)

    request_form_data = {} # Stores POST form data to persist values in the form

    # --- Fetch LED data for the LED control section ---
    led_data = {}
    try:
        cursor.execute("SELECT id, value FROM tbl_bin")
        led_rows = cursor.fetchall()
        led_data = {row['id']: row['value'] for row in led_rows}
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูล LED: {err}", 'danger')
        # Continue with empty led_data if there's an error

    # If it's a POST request, store form data and update barcode_id_filter if necessary
    if request.method == 'POST':
        request_form_data = request.form.to_dict() # Store all POST form data
        if request.form.get('action') == 'add_disquantity':
            # If adding disquantity, use the barcode_id from the form
            barcode_id_filter = request.form.get('barcode_id_for_disquantity', barcode_id_filter)
        elif request.form.get('action') == 'search':
            # If performing a search, use the barcode_id from the search input
            barcode_id_filter = request.form.get('barcode_id_filter_input', barcode_id_filter)
        elif request.form.get('action') == 'reset_all_leds': # This action name is used by the button in HTML
            # Handle reset specific LEDs action (category_id 1-5)
            conn_reset = get_db_connection()
            if not conn_reset:
                flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูลเพื่อรีเซ็ต LED.", 'danger')
                return redirect(url_for('bin'))
            try:
                cursor_reset = conn_reset.cursor()
                # Set value to 0 for specific category_ids as requested
                cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                conn_reset.commit()
                # Set session flag to indicate reset button was pressed
                session['reset_leds_pressed'] = True
                flash("รีเซ็ตสถานะ LED ที่มี category_id 1-5 ทั้งหมดเป็น 'ปิด' สำเร็จ.", 'success')
            except mysql.connector.Error as err:
                flash(f"เกิดข้อผิดพลาดในการรีเซ็ต LED: {err}", 'danger')
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
            return redirect(url_for('bin'))


    # --- Logic for incrementing Disquantity (+1) ---
    if request.method == "POST" and request.form.get('action') == 'add_disquantity':
        barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
        products_id_to_disquantity = request.form.get('products_id_to_disquantity')

        if not barcode_id_to_search or not products_id_to_disquantity:
            flash("กรุณาระบุรหัสบาร์โค้ดและรหัสสินค้าที่ต้องการเพิ่มจำนวนทิ้ง.", 'danger')
            # Re-render with existing form data and filter, and LED data
            return render_template("bin.html", orders=[], barcode_id_filter=barcode_id_filter, request_form_data=request_form_data, led_data=led_data)

        # Check if the reset LED button was pressed before allowing disquantity increment
        if not session.get('reset_leds_pressed'):
            flash("กรุณากดปุ่ม 'รีเซ็ตสถานะ LED (category_id 1-5)' ก่อนที่จะเพิ่มจำนวนทิ้ง.", 'warning')
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))

        try:
            # Search for the item in tbl_order matching barcode_id and products_id
            # Now including 'p.category_id' from tbl_products
            cursor.execute("""
                SELECT o.id, o.quantity, o.disquantity, o.products_name, o.products_id, p.stock, p.category_id
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s AND o.products_id = %s
            """, (barcode_id_to_search, products_id_to_disquantity))
            order_item_to_update = cursor.fetchone()

            if order_item_to_update:
                current_quantity = order_item_to_update['quantity']
                current_disquantity = order_item_to_update['disquantity']
                # current_product_stock = order_item_to_update['stock'] # Not used for this validation
                product_id = order_item_to_update['products_id']
                # Get category_id from tbl_products, assuming it exists there
                category_id_for_bin = order_item_to_update.get('category_id')

                if category_id_for_bin is None:
                    flash(f"ไม่พบ category_id สำหรับสินค้า '{order_item_to_update['products_name']}'. ไม่สามารถอัปเดต bin ได้.", 'danger')
                    conn.close()
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))


                proposed_disquantity = current_disquantity + 1

                # Check if the proposed disquantity does not exceed the total quantity
                if proposed_disquantity <= current_quantity:
                    # Update disquantity in tbl_order
                    cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                   (proposed_disquantity, order_item_to_update['id']))

                    # ขั้นตอนที่ 1: ตั้งค่า LED อื่นๆ ทั้งหมดเป็น OFF (value = 0)
                    # เพื่อให้แน่ใจว่ามี LED เพียงตัวเดียวเท่านั้นที่เปิดอยู่
                    cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s", (category_id_for_bin,))

                    # ขั้นตอนที่ 2: ตั้งค่า LED ที่ถูกเลือกเป็น ON (value = 1)
                    # ตามคำขอของผู้ใช้ LED ที่ถูกเลือกควรจะเปิดเสมอ
                    cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))

                    conn.commit()
                    # Reset the session flag after successful disquantity increment
                    session['reset_leds_pressed'] = False
                    flash(f"เพิ่มจำนวนทิ้งสินค้า '{order_item_to_update['products_name']}' (รหัสสินค้า: {products_id_to_disquantity}) สำเร็จ. สถานะ bin (category_id: {category_id_for_bin}) ได้รับการอัปเดตแล้ว.", 'success')
                else:
                    flash(f"ไม่สามารถเพิ่มจำนวนทิ้งได้เกินจำนวนสินค้าที่มีอยู่ ({current_quantity} ชิ้น) สำหรับสินค้า '{order_item_to_update['products_name']}'", 'danger')
            else:
                flash("ไม่พบรายการสินค้าที่ตรงกันสำหรับรหัสบาร์โค้ดและรหัสสินค้าที่ระบุ.", 'danger')

        except mysql.connector.Error as err:
            flash(f"เกิดข้อผิดพลาดในการดำเนินการ: {err}", 'danger')
            conn.rollback() # Rollback in case of error
        finally:
            # Always close the database connection
            if conn:
                conn.close()
            # Redirect back to the bin page with the current barcode_id_filter to show filtered results
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))

    # --- Main logic for displaying order table (GET requests or POST that is not 'add_disquantity' action) ---
    try:
        if barcode_id_filter:
            # Fetch only items matching the barcode_id filter
            base_query = """
                SELECT o.*, p.price, p.products_name, p.category_id
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s
            """
            query_params = [barcode_id_filter]

            # Add email filter for 'member' role
            if session.get('role') == 'member':
                base_query += " AND o.email = %s"
                query_params.append(session['email'])

            base_query += " ORDER BY o.id DESC"
            cursor.execute(base_query, tuple(query_params))
            orders_data = cursor.fetchall()
        else:
            # If no barcode_id_filter, display nothing
            orders_data = [] # Display empty table if no search

    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลคำสั่งซื้อ: {err}", 'danger')
    finally:
        if conn:
            conn.close()

    current_auto_order_id = "N/A" 

    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=barcode_id_filter,
                           request_form_data=request_form_data, # Send filled form data
                           current_auto_order_id=current_auto_order_id, # Can be removed
                           led_data=led_data # Pass LED data to the template
                           )

if __name__ == '__main__':
    threading.Thread(target=check_and_control_led_loop, daemon=True).start()
    app.run(debug=True, host='0.0.0.0')
