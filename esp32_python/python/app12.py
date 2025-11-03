from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
import requests
import threading
import time
from functools import wraps # Import wraps for decorator

app = Flask(__name__)
app.secret_key = 'your_secret_key_here' # ตั้งค่า secret key สำหรับ session และ flash messages

ESP32_IP = "http://192.168.114.133" # ตรวจสอบให้แน่ใจว่า IP Address นี้ถูกต้อง

DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'project_bin'
}

# เปลี่ยนชื่อจาก LED_PINS เป็น SERVO_PINS เพื่อความชัดเจน
SERVO_PINS = [12, 13, 14, 26, 27] # ต้องแน่ใจว่าตรงกับ servoPins ในโค้ด ESP32
servo_cache = {} # เปลี่ยนชื่อจาก led_cache เป็น servo_cache

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

def check_and_control_servo_loop(): # เปลี่ยนชื่อฟังก์ชันให้สื่อถึง Servo
    """
    Continuously check servo states from the database and control ESP32.
    Handles database and network errors, providing logging.
    """
    global servo_cache # ใช้ servo_cache แทน led_cache
    global last_error_message, error_occurrence_count, normal_status_printed

    while True:
        try:
            # --- Normal operation (no error) ---
            conn = get_db_connection()
            if not conn:
                raise Exception("Failed to get database connection in servo loop.")

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
                servo_id = row["id"] # เปลี่ยนชื่อตัวแปรเป็น servo_id
                value = row["value"]
                action = "on" if value == 1 else "off"

                # ตรวจสอบสถานะใน cache และสั่งงานเมื่อมีการเปลี่ยนแปลง
                if servo_id not in servo_cache or servo_cache[servo_id] != value:
                    servo_cache[servo_id] = value
                    try:
                        # เปลี่ยน URL จาก /led เป็น /servo
                        requests.get(f"{ESP32_IP}/servo{servo_id}/{action}", timeout=2)
                        print(f"[Update] Servo {servo_id} → {action.upper()}") # เปลี่ยนข้อความแสดงผล
                    except requests.exceptions.RequestException as req_e:
                        # Separate ESP32 connection error
                        current_error = f"Cannot control ESP32 for Servo {servo_id} - {req_e}" # เปลี่ยนข้อความแสดงผล
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

@app.route('/toggle_servo/<int:servo_id>', methods=['POST']) # เปลี่ยน route และชื่อตัวแปร
def toggle_servo(servo_id): # เปลี่ยนชื่อฟังก์ชัน
    """
    Toggle the state of a specific servo and update the database.
    This function is now called from the /bin page.
    Modified: When a servo is selected, it will be turned ON (30 degrees), and all other servos will be turned OFF (0 degrees).
    """
    if servo_id in SERVO_PINS: # ใช้ SERVO_PINS
        conn = get_db_connection()
        if not conn:
            flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
            return redirect(url_for('bin'))

        try:
            cursor = conn.cursor(dictionary=True) # ใช้ cursor แบบ dictionary เพื่อความสอดคล้องกับโค้ดเดิม

            # ขั้นตอนที่ 1: ตั้งค่า Servo อื่นๆ ทั้งหมดเป็น OFF (value = 0)
            # เพื่อให้แน่ใจว่ามี Servo เพียงตัวเดียวเท่านั้นที่เปิดอยู่
            cursor.execute("UPDATE tbl_bin SET value = 0 WHERE id != %s", (servo_id,))

            # ขั้นตอนที่ 2: ตั้งค่า Servo ที่ถูกเลือกเป็น ON (value = 1)
            # ตามคำขอของผู้ใช้ Servo ที่ถูกเลือกควรจะเปิดเสมอ
            cursor.execute("UPDATE tbl_bin SET value = 1 WHERE id = %s", (servo_id,))
            conn.commit()
            flash(f"เปิด Servo {servo_id} และปิด Servo อื่นๆ ทั้งหมดสำเร็จ.", 'success') # เปลี่ยนข้อความแสดงผล
        except Exception as e:
            flash(f"เกิดข้อผิดพลาดในการสลับสถานะ Servo: {e}", 'danger') # เปลี่ยนข้อความแสดงผล
            conn.rollback()
        finally:
            conn.close()
    else:
        flash(f"Servo ID {servo_id} ไม่ถูกต้อง.", 'danger') # เปลี่ยนข้อความแสดงผล
    return redirect(url_for('bin')) # เปลี่ยนเส้นทางกลับหน้า bin เสมอ

@app.route("/", methods=["GET", "POST"]) # Changed route to '/'
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    """
    Handle the bin management page, including searching orders and
    incrementing 'disquantity' for items, updating tbl_bin,
    and displaying Servo control. This is now the default homepage.
    """
    conn = get_db_connection()
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("bin.html", orders=[], barcode_id_filter='', request_form_data={}, servo_data={}) # เปลี่ยน led_data เป็น servo_data

    cursor = conn.cursor(dictionary=True)
    orders_data = [] # Data for order items to display
    barcode_id_filter = request.args.get('barcode_id_filter', '') # For GET requests (search/reset)

    request_form_data = {} # Stores POST form data to persist values in the form

    # --- Fetch Servo data for the Servo control section ---
    servo_data = {} # เปลี่ยน led_data เป็น servo_data
    try:
        cursor.execute("SELECT id, value FROM tbl_bin")
        servo_rows = cursor.fetchall() # เปลี่ยน led_rows เป็น servo_rows
        servo_data = {row['id']: row['value'] for row in servo_rows} # เปลี่ยน led_data เป็น servo_data
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูล Servo: {err}", 'danger') # เปลี่ยนข้อความแสดงผล
        # Continue with empty servo_data if there's an error

    # If it's a POST request, store form data and update barcode_id_filter if necessary
    if request.method == 'POST':
        request_form_data = request.form.to_dict() # Store all POST form data
        if request.form.get('action') == 'add_disquantity':
            # If adding disquantity, use the barcode_id from the form
            barcode_id_filter = request.form.get('barcode_id_for_disquantity', barcode_id_filter)
        elif request.form.get('action') == 'search':
            # If performing a search, use the barcode_id from the search input
            barcode_id_filter = request.form.get('barcode_id_filter_input', barcode_id_filter)
        elif request.form.get('action') == 'reset_all_servos': # เปลี่ยนชื่อ action สำหรับปุ่มรีเซ็ต
            # Handle reset specific Servos action (category_id 1-5)
            conn_reset = get_db_connection()
            if not conn_reset:
                flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูลเพื่อรีเซ็ต Servo.", 'danger') # เปลี่ยนข้อความแสดงผล
                return redirect(url_for('bin'))
            try:
                cursor_reset = conn_reset.cursor()
                stored_item = session.get('stored_item_for_increment')

                if stored_item:
                    barcode_id_to_search = stored_item['barcode_id']
                    products_id_to_disquantity = stored_item['products_id']
                    category_id_for_bin = stored_item['category_id']

                    # Search for the item in tbl_order matching barcode_id and products_id
                    cursor_reset.execute("""
                        SELECT o.id, o.quantity, o.disquantity, o.products_name, o.products_id
                        FROM tbl_order o
                        JOIN tbl_products p ON o.products_id = p.products_id
                        WHERE o.barcode_id = %s AND o.products_id = %s
                    """, (barcode_id_to_search, products_id_to_disquantity))
                    order_item_to_update = cursor_reset.fetchone()

                    if order_item_to_update:
                        current_quantity = order_item_to_update[1] # quantity
                        current_disquantity = order_item_to_update[2] # disquantity
                        product_name = order_item_to_update[3] # products_name

                        proposed_disquantity = current_disquantity + 1

                        if proposed_disquantity <= current_quantity:
                            # Update disquantity in tbl_order
                            cursor_reset.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                               (proposed_disquantity, order_item_to_update[0])) # order_id

                            # Reset all Servos for category_id 1-5 to 0
                            cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                            conn_reset.commit()

                            # Clear stored item and flags after successful increment and reset
                            session.pop('stored_item_for_increment', None) 
                            session['first_click_done'] = False 
                            session['reset_done'] = True # Mark reset as done for this cycle
                            flash(f"เพิ่มจำนวนทิ้งสินค้า '{product_name}' (รหัสสินค้า: {products_id_to_disquantity}) สำเร็จ และรีเซ็ตสถานะ Servo แล้ว.", 'success') # เปลี่ยนข้อความแสดงผล
                        else:
                            flash(f"ไม่สามารถเพิ่มจำนวนทิ้งได้เกินจำนวนสินค้าที่มีอยู่ ({current_quantity} ชิ้น) สำหรับสินค้า '{product_name}'", 'danger')
                            # Still reset Servos even if disquantity not incremented due to limit
                            cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                            conn_reset.commit()
                            session.pop('stored_item_for_increment', None)
                            session['first_click_done'] = False
                            session['reset_done'] = True
                    else:
                        flash("ไม่พบรายการสินค้าที่ตรงกันสำหรับรหัสบาร์โค้ดและรหัสสินค้าที่ถูกจัดเก็บ.", 'danger')
                        # If no stored item found, just reset Servos
                        cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                        conn_reset.commit()
                        session.pop('stored_item_for_increment', None)
                        session['first_click_done'] = False
                        session['reset_done'] = True
                else:
                    flash("ไม่มีสินค้าที่ถูกเลือกไว้สำหรับการเพิ่มจำนวนทิ้ง. กำลังรีเซ็ตสถานะ Servo เท่านั้น.", 'info') # เปลี่ยนข้อความแสดงผล
                    # If no stored item, just reset Servos
                    cursor_reset.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
                    conn_reset.commit()
                    session.pop('stored_item_for_increment', None)
                    session['first_click_done'] = False
                    session['reset_done'] = True # Mark reset as done
            except mysql.connector.Error as err:
                flash(f"เกิดข้อผิดพลาดในการรีเซ็ต Servo และเพิ่มจำนวนทิ้ง: {err}", 'danger') # เปลี่ยนข้อความแสดงผล
                conn_reset.rollback()
            finally:
                if conn_reset:
                    conn_reset.close()
            return redirect(url_for('bin'))


    # --- Logic for handling 'add_disquantity' (First Click to activate Servo) ---
    if request.method == "POST" and request.form.get('action') == 'add_disquantity':
        barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
        products_id_to_disquantity = request.form.get('products_id_to_disquantity')

        if not barcode_id_to_search or not products_id_to_disquantity:
            flash("กรุณาระบุรหัสบาร์โค้ดและรหัสสินค้าที่ต้องการเพิ่มจำนวนทิ้ง.", 'danger')
            # Re-render with existing form data and filter, and Servo data
            return render_template("bin.html", orders=[], barcode_id_filter=barcode_id_filter, request_form_data=request_form_data, servo_data=servo_data) # เปลี่ยน led_data เป็น servo_data

        try:
            # Find the category_id for the selected product
            cursor.execute("""
                SELECT p.category_id, o.products_name
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s AND o.products_id = %s
            """, (barcode_id_to_search, products_id_to_disquantity))
            item_info = cursor.fetchone()

            if item_info:
                category_id_for_bin = item_info.get('category_id')
                product_name = item_info.get('products_name')

                if category_id_for_bin is None:
                    flash(f"ไม่พบ category_id สำหรับสินค้า '{product_name}'. ไม่สามารถอัปเดต bin ได้.", 'danger')
                    conn.close()
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))
                
                # Store item for subsequent increment by the RESET button
                session['stored_item_for_increment'] = {
                    'barcode_id': barcode_id_to_search,
                    'products_id': products_id_to_disquantity,
                    'category_id': category_id_for_bin
                }
                session['first_click_done'] = True # Mark first click as done
                session['reset_done'] = False # Reset has not been done yet for this cycle

                # Turn on the specific Servo and turn off others (category_id 1-5)
                cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id != %s AND category_id IN (1, 2, 3, 4, 5)", (category_id_for_bin,))
                cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", (category_id_for_bin,))
                conn.commit()
                flash(f"Servo สำหรับสินค้า '{product_name}' (category_id: {category_id_for_bin}) หมุนแล้ว. กรุณากดปุ่ม 'รีเซ็ตสถานะ Servo (category_id 1-5)' เพื่อยืนยันการเพิ่มจำนวนทิ้ง.", 'info') # เปลี่ยนข้อความแสดงผล
            else:
                flash("ไม่พบรายการสินค้าที่ตรงกันสำหรับรหัสบาร์โค้ดและรหัสสินค้าที่ระบุ.", 'danger')
        except mysql.connector.Error as err:
            flash(f"เกิดข้อผิดพลาดในการดำเนินการ: {err}", 'danger')
            conn.rollback()
        finally:
            if conn:
                conn.close()
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

    current_auto_order_id = "N/A" # This variable seems unused or needs to be populated

    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=barcode_id_filter,
                           request_form_data=request_form_data, # Send filled form data
                           current_auto_order_id=current_auto_order_id, # Can be removed
                           servo_data=servo_data # Pass Servo data to the template
                           )
@app.route('/sensor_reset', methods=['GET'])
def sensor_reset():
    """
    Endpoint สำหรับ ESP32 เมื่อเซ็นเซอร์ IR ตรวจจับวัตถุผ่าน
    → อัปเดต database (category_id 1–5) และส่งคำสั่ง OFF ไปยัง ESP32 โดยตรง
    และเพิ่มค่า disquantity + 1 โดยอ้างอิงจาก stored_item_for_increment ใน session.
    """
    print("[Sensor Trigger] IR detected → Processing disquantity increment and resetting servos.")

    conn = get_db_connection()
    if not conn:
        print("Error: Database connection failed during sensor reset.")
        return "Database connection failed", 500

    try:
        cursor = conn.cursor()
        
        # ดึงข้อมูลที่เก็บไว้ใน session ตอนที่ผู้ใช้กด "เพิ่มจำนวนทิ้ง"
        stored_item = session.get('stored_item_for_increment')

        if stored_item:
            barcode_id_to_search = stored_item['barcode_id']
            products_id_to_disquantity = stored_item['products_id']

            # ค้นหารายการสินค้าใน tbl_order เพื่อตรวจสอบและอัปเดต disquantity
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
                    # อัปเดต disquantity ใน tbl_order
                    cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                   (proposed_disquantity, order_id))
                    print(f"[Disquantity Update] Increased disquantity for item '{product_name}' to {proposed_disquantity}")
                else:
                    print(f"[Warning] Cannot increase disquantity for '{product_name}'. Reached maximum quantity.")

            # Clear stored item and flags after processing
            session.pop('stored_item_for_increment', None)
            session['first_click_done'] = False
            session['reset_done'] = True
        else:
            print("[Warning] No stored item found in session. Disquantity not incremented.")
            
        # ส่วนนี้ยังคงทำหน้าที่เดิม คือการรีเซ็ต Servo
        cursor.execute("UPDATE tbl_bin SET value = 0 WHERE category_id IN (1, 2, 3, 4, 5)")
        conn.commit()

        # ส่งคำสั่ง OFF ไปยัง ESP32 โดยตรงสำหรับ Servo ที่เกี่ยวข้อง
        servo_ids = [12, 13, 14, 26, 27]
        for sid in servo_ids:
            try:
                requests.get(f"{ESP32_IP}/servo{sid}/off", timeout=2)
                print(f"[Forced Update] Servo {sid} → OFF")
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
    threading.Thread(target=check_and_control_servo_loop, daemon=True).start() # เปลี่ยนชื่อฟังก์ชัน
    app.run(debug=True, host='0.0.0.0')
