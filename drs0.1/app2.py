from flask import Flask, render_template, request, redirect, url_for, session, Response, make_response, flash, jsonify
from xhtml2pdf import pisa
import mysql.connector
import csv
import random
import string
import sys
from io import StringIO, BytesIO
from datetime import datetime
from functools import wraps

# Imports for image generation (although not directly used in the provided logic for now)
from PIL import Image, ImageDraw, ImageFont
import requests
import os 

app = Flask(__name__)
app.secret_key = 'trash-for-coin-secret-key-2025' # *** สำคัญมาก: เปลี่ยนเป็นคีย์ลับที่ปลอดภัยของคุณ ***

# --- Barcode Encoding/Decoding Functions ---
def encode(x: int) -> int:
    a = 982451653
    b = 1234567891234
    m = 10000000000039 # จำนวนเฉพาะที่ใกล้เคียง 10^13
    return (a * x + b) % m

def decode(y: int) -> int:
    a = 982451653
    b = 1234567891234
    m = 10000000000039 # ต้องเป็นค่าเดียวกับ m ใน encode
    a_inv = pow(a, m - 2, m) # หา inverse ของ a mod m โดยใช้ Fermat's Little Theorem
    return (a_inv * (y - b)) % m

# --- Database Connection ---
def get_db_connection():
    """
    Establishes a connection to the MySQL database.
    Returns the connection object or None if connection fails.
    """
    try:
        conn = mysql.connector.connect(
            host="192.168.1.128",
            user="root",
            password="1234", 
            database="drs" # Make sure this matches your database name
        )
        return conn
    except mysql.connector.Error as err:
        print(f"Error connecting to database: {err}")
        return None

# --- Helper functions for Viewer's dynamic store ---
def generate_unique_store_id(conn, cursor):
    """Generates a unique store ID and creates a new store for the viewer."""
    max_attempts = 100
    for _ in range(max_attempts):
        # Generate a random 6-digit number for the store ID
        new_store_id = random.randint(100000, 999999)
        new_store_name = f"ร้านค้าสาธิต Viewer {new_store_id}"
        new_address = "ที่อยู่จำลอง"
        new_phone = "000-000-0000"

        try:
            # Check if store_id already exists (highly unlikely for random 6-digit)
            cursor.execute("SELECT store_id FROM tbl_stores WHERE store_id = %s", (new_store_id,))
            if cursor.fetchone():
                continue # Try again if ID exists

            cursor.execute("INSERT INTO tbl_stores (store_id, store_name, address, phone) VALUES (%s, %s, %s, %s)",
                           (new_store_id, new_store_name, new_address, new_phone))
            conn.commit()
            return new_store_id, new_store_name
        except mysql.connector.Error as err:
            conn.rollback()
            print(f"Error creating new viewer store: {err}")
    
    # Fallback if too many attempts failed (should ideally not happen)
    raise Exception("ไม่สามารถสร้าง store_id ที่ไม่ซ้ำกันได้หลังจากพยายามหลายครั้ง")

def delete_viewer_store_and_data(store_id):
    """Deletes all data associated with a viewer's store_id and the store itself."""
    conn = get_db_connection()
    if not conn:
        print("Error: Could not connect to DB to delete viewer store data.")
        return False
    cursor = conn.cursor()
    try:
        # Before deleting the store, set foreign keys in dependent tables to NULL
        # (Assuming ON DELETE SET NULL is enabled for these relationships in DB)
        cursor.execute("UPDATE tbl_order SET store_id = NULL WHERE store_id = %s", (store_id,))
        cursor.execute("UPDATE tbl_products SET store_id = NULL WHERE store_id = %s", (store_id,))
        cursor.execute("UPDATE tbl_category SET store_id = NULL WHERE store_id = %s", (store_id,))
        cursor.execute("UPDATE tbl_users SET store_id = NULL WHERE store_id = %s", (store_id,)) # Users can also be tied to a store

        # Finally, delete the store itself
        cursor.execute("DELETE FROM tbl_stores WHERE store_id = %s", (store_id,))
        conn.commit()
        print(f"Viewer store (ID: {store_id}) and its associated data deleted successfully.")
        return True
    except mysql.connector.Error as err:
        conn.rollback()
        print(f"Error deleting viewer store (ID: {store_id}) and data: {err}")
        return False
    finally:
        if cursor: # Ensure cursor is closed
            cursor.close()
        if conn:
            conn.close()


# --- Role-based Access Control (RBAC) Decorators ---
def role_required(allowed_roles):
    """
    Decorator to restrict access to routes based on user roles.
    If the user is not logged in, they are redirected to the login page.
    If the user's role is not in the allowed_roles list, they are redirected to the index page.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'loggedin' not in session:
                flash('โปรดเข้าสู่ระบบเพื่อเข้าถึงหน้านี้', 'danger')
                return redirect(url_for('login'))
            
            # Allow root_admin and administrator to access all allowed_roles
            if session.get('role') in ['root_admin', 'administrator']:
                return f(*args, **kwargs)

            # For other roles, check if their role is in allowed_roles
            if session.get('role') not in allowed_roles:
                flash(f'คุณไม่มีสิทธิ์เข้าถึงหน้านี้ ยศของคุณคือ {session.get("role")}', 'danger')
                return redirect(url_for('index'))
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# --- Routes ---
@app.route("/")
@app.route("/index")
def index():
    """
    หน้าแรกของระบบ แสดงสถิติการใช้งานโดยรวมจากฐานข้อมูล
    """
    # สร้าง Dictionary สำหรับเก็บค่าสถิติ
    stats = {
        'total_users': 0,
        'recycled_waste': 0,
        'satisfaction': 0
    }

    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()

            # 1. ดึงจำนวนผู้ใช้งานทั้งหมดจาก tbl_users
            cursor.execute("SELECT COUNT(*) FROM tbl_users")
            # fetchone() จะ trả về một tuple, เราจึงเลือก item แรก ([0])
            total_users = cursor.fetchone()[0]
            stats['total_users'] = total_users

            # 2. ดึงผลรวมของ quantity และ disquantity จาก tbl_order
            cursor.execute("SELECT SUM(quantity), SUM(disquantity) FROM tbl_order")
            order_data = cursor.fetchone()
            total_quantity, total_disquantity = order_data

            # ตรวจสอบว่าค่าที่ได้ไม่เป็น None (กรณีตารางว่าง)
            total_quantity = total_quantity or 0
            total_disquantity = total_disquantity or 0
            
            # 3. คำนวณค่าขยะที่รีไซเคิล (disquantity ทั้งหมด)
            stats['recycled_waste'] = int(total_disquantity)

            # 4. คำนวณค่าความพึงพอใจ
            if total_quantity > 0:
                satisfaction_rate = (100 / total_quantity) * total_disquantity
            else:
                # ป้องกันการหารด้วยศูนย์ถ้าไม่มี quantity เลย
                satisfaction_rate = 0
            
            # จัดรูปแบบให้เป็นจำนวนเต็ม
            stats['satisfaction'] = int(satisfaction_rate)

        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            if conn.is_connected():
                cursor.close()
                conn.close()

    # ส่งค่า stats ไปยัง template 'index.html'
    return render_template("index.html", stats=stats)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Handles user login.
    Authenticates user credentials against tbl_users table.
    Sets session variables upon successful login and updates online status.
    For 'viewer' role, a temporary store_id is created and assigned.
    """
    msg = ''
    if request.method == 'POST' and 'email' in request.form and 'password' in request.form:
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = None # Initialize cursor to None
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                # Fetch store_id along with other user details
                cursor.execute('SELECT *, store_id FROM tbl_users WHERE email = %s AND password = %s', (email, password,))
                account = cursor.fetchone()
                
                if account:
                    session['loggedin'] = True
                    session['id'] = account['id']
                    session['email'] = account['email']
                    session['firstname'] = account['firstname']
                    session['lastname'] = account['lastname']
                    session['role'] = account['role']
                    
                    # Logic for Viewer to create a new, temporary store_id
                    if account['role'] == 'viewer':
                        try:
                            new_temp_store_id, new_temp_store_name = generate_unique_store_id(conn, cursor)
                            session['store_id'] = new_temp_store_id
                            session['store_name'] = new_temp_store_name
                            print(f"Viewer (ID: {account['id']}) logged in and assigned to new temp store: {new_temp_store_id} - {new_temp_store_name}")
                        except Exception as e:
                            print(f"Failed to create temp store for viewer: {e}")
                            flash("เกิดข้อผิดพลาดในการสร้างร้านค้าจำลอง. โปรดลองใหม่อีกครั้ง.", 'danger')
                            return redirect(url_for('login'))
                    else:
                        session['store_id'] = account['store_id']
                        # Fetch actual store name for session if store_id exists
                        if account['store_id']:
                            cursor.execute("SELECT store_name FROM tbl_stores WHERE store_id = %s", (account['store_id'],))
                            actual_store_info = cursor.fetchone()
                            session['store_name'] = actual_store_info['store_name'] if actual_store_info else 'ไม่มีร้านค้า'
                        else:
                            session['store_name'] = 'ไม่มีร้านค้า' # For root_admin/administrator/member without a specific store

                    # Update user's online status to TRUE
                    cursor.execute("UPDATE tbl_users SET is_online = TRUE WHERE id = %s", (account['id'],))
                    conn.commit()

                    msg = 'เข้าสู่ระบบสำเร็จ!'
                    flash(msg, 'success')
                    return redirect(url_for('index'))
                else:
                    msg = 'อีเมลหรือรหัสผ่านไม่ถูกต้อง!'
                    flash(msg, 'danger')
            except mysql.connector.Error as err:
                msg = f"เกิดข้อผิดพลาดในการเข้าสู่ระบบ: {err}"
                flash(msg, 'danger')
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
    return render_template('login.html', msg=msg)

@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Handles new user registration.
    Inserts new user data into tbl_users table with 'member' role by default.
    Checks for existing email addresses.
    """
    msg = ''
    if request.method == 'POST' and 'firstname' in request.form and 'lastname' in request.form and 'email' in request.form and 'password' in request.form:
        firstname = request.form['firstname']
        lastname = request.form['lastname']
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = None # Initialize cursor to None
        if conn:
            try:
                cursor = conn.cursor(dictionary=True)
                cursor.execute('SELECT * FROM tbl_users WHERE email = %s', (email,))
                account = cursor.fetchone()
                
                if account:
                    msg = 'บัญชีนี้มีอยู่แล้ว!'
                    flash(msg, 'danger')
                elif not firstname or not lastname or not email or not password:
                    msg = 'กรุณากรอกข้อมูลให้ครบถ้วน!'
                    flash(msg, 'danger')
                else:
                    # Default role is 'member', store_id is NULL by default or can be set by admin later
                    # New user is offline by default
                    cursor.execute('INSERT INTO tbl_users (firstname, lastname, email, password, role, is_online) VALUES (%s, %s, %s, %s, %s, FALSE)', (firstname, lastname, email, password, 'member',))
                    conn.commit()
                    msg = 'คุณสมัครสมาชิกสำเร็จแล้ว!'
                    flash(msg, 'success')
                    return redirect(url_for('login'))
            except mysql.connector.Error as err:
                msg = f"เกิดข้อผิดพลาดในการลงทะเบียน: {err}"
                flash(msg, 'danger')
            finally:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
    elif request.method == 'POST':
        msg = 'กรุณากรอกข้อมูลให้ครบถ้วน!'
        flash(msg, 'danger')
    return render_template('register.html', msg=msg)
import mysql.connector # สมมติว่ามีการ import นี้อยู่แล้ว
from flask import request, render_template, session, redirect, url_for, flash
# สมมติว่ามีฟังก์ชัน get_db_connection และ role_required (ไม่ได้ให้มา)
# --- Route สำหรับหน้าโปรไฟล์ (อัปเดตข้อมูล) ---
@app.route('/profile', methods=['GET', 'POST'])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def profile():
    """
    Allows logged-in users to manage their profile information and view their stats.
    """
    msg = ''
    order_count = 0
    total_quantity = 0
    recycling_rate = 0

    # Calculate user statistics
    if 'loggedin' in session and 'email' in session:
        stats_conn = get_db_connection()
        if stats_conn:
            try:
                cursor = stats_conn.cursor()
                user_email = session['email']
                cursor.execute('SELECT COUNT(*) FROM tbl_order WHERE email = %s', (user_email,))
                order_count = cursor.fetchone()[0] or 0
                cursor.execute('SELECT SUM(quantity), SUM(disquantity) FROM tbl_order WHERE email = %s', (user_email,))
                sum_result = cursor.fetchone()
                db_total_quantity = sum_result[0] or 0
                db_total_disquantity = sum_result[1] or 0
                total_quantity = int(db_total_quantity)
                if db_total_quantity > 0:
                    recycling_rate = int((100 / float(db_total_quantity)) * float(db_total_disquantity))
            except mysql.connector.Error as err:
                flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลสถิติ: {err}", 'danger')
            finally:
                if 'cursor' in locals() and cursor:
                    cursor.close()
                stats_conn.close()

    # Handle profile update form submission
    if request.method == 'POST':
        if session.get('role') == 'viewer':
            flash("คุณไม่มีสิทธิ์อัปเดตโปรไฟล์ในฐานะ Viewer", 'info')
            return redirect(url_for('profile'))

        new_firstname = request.form['firstname']
        new_lastname = request.form['lastname']
        new_email = request.form['email']
        new_password = request.form.get('password')
        current_password = request.form.get('current_password')
        
        is_email_changed = new_email != session['email']
        is_password_set = new_password and len(new_password.strip()) > 0
        needs_security_check = is_email_changed or is_password_set

        update_conn = get_db_connection()
        if update_conn:
            try:
                cursor = update_conn.cursor(dictionary=True)
                if needs_security_check:
                    if not current_password:
                        flash('ต้องกรอกรหัสผ่านปัจจุบันเพื่อบันทึกการเปลี่ยนแปลง', 'danger')
                        return redirect(url_for('profile'))
                    
                    cursor.execute('SELECT password FROM tbl_users WHERE id = %s', (session['id'],))
                    user_data = cursor.fetchone()
                    
                    # !! SECURITY NOTE: In a real app, use a secure hash comparison
                    if not user_data or user_data['password'] != current_password:
                        flash('รหัสผ่านปัจจุบันไม่ถูกต้อง!', 'danger')
                        return redirect(url_for('profile'))

                if is_email_changed:
                    cursor.execute('SELECT id FROM tbl_users WHERE email = %s AND id != %s', (new_email, session['id']))
                    if cursor.fetchone():
                        flash('อีเมลนี้มีผู้ใช้งานอื่นแล้ว!', 'danger')
                        return redirect(url_for('profile'))
                
                # Database Update
                update_fields = {'firstname': new_firstname, 'lastname': new_lastname, 'email': new_email}
                if is_password_set:
                    # !! SECURITY NOTE: In a real app, hash the new_password here
                    update_fields['password'] = new_password
                
                update_query_parts = [f"{field} = %s" for field in update_fields.keys()]
                update_params = list(update_fields.values())
                update_params.append(session['id'])
                
                update_query = f"UPDATE tbl_users SET {', '.join(update_query_parts)} WHERE id = %s"
                cursor.execute(update_query, tuple(update_params))
                update_conn.commit()
                
                # Update Session
                session.update(update_fields)
                flash('อัปเดตข้อมูลโปรไฟล์สำเร็จ!', 'success')
                return redirect(url_for('profile'))

            except mysql.connector.Error as err:
                flash(f"เกิดข้อผิดพลาดในการอัปเดต: {err}", 'danger')
            finally:
                if 'cursor' in locals() and cursor:
                    cursor.close()
                update_conn.close()
        else:
            flash('ไม่สามารถเชื่อมต่อฐานข้อมูลได้', 'danger')

    return render_template('profile.html', msg=msg, session=session, order_count=order_count, total_quantity=total_quantity, recycling_rate=recycling_rate)

# ต้องแน่ใจว่ามีการนำเข้าเหล่านี้ในไฟล์ของคุณ
from flask import request, jsonify, session
# import mysql.connector # หรือไลบรารีฐานข้อมูลที่คุณใช้

# @role_required เป็น decorator ที่คุณกำหนดเอง
# get_db_connection เป็นฟังก์ชันที่คุณกำหนดเอง

# --- Route สำหรับการลบบัญชี (โค้ดที่สมบูรณ์) ---
@app.route('/delete_account', methods=['POST'])
@role_required(['root_admin', 'administrator', 'moderator', 'member'])
def delete_account():
    """
    Handles user account deletion. Verifies password before deleting.
    """
    if 'loggedin' not in session:
        return jsonify({'status': 'error', 'message': 'Authentication required'}), 401
    
    # 🚨 แก้ไข: ตรวจสอบ Content-Type ก่อน
    if not request.is_json:
        # ตอบกลับด้วยข้อผิดพลาด 415 (Unsupported Media Type) หากไม่ใช่ application/json
        return jsonify({'status': 'error', 'message': 'ประเภทสื่อไม่ถูกต้อง: ต้องส่งคำขอเป็น application/json'}), 415

    try:
        # พยายามโหลดข้อมูล JSON
        data = request.json
        password = data.get('password')
    except Exception as e:
        # กรณี JSON ไม่สมบูรณ์ (Malformed JSON)
        return jsonify({'status': 'error', 'message': f'รูปแบบข้อมูล JSON ไม่ถูกต้อง: {e}'}), 400

    if not password:
        return jsonify({'status': 'error', 'message': 'กรุณากรอกรหัสผ่านเพื่อยืนยัน'}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({'status': 'error', 'message': 'ไม่สามารถเชื่อมต่อฐานข้อมูล'}), 500
    
    # ดำเนินการลบ
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)
        
        # 1. ดึงรหัสผ่านที่เก็บไว้เพื่อตรวจสอบ
        cursor.execute('SELECT password FROM tbl_users WHERE id = %s', (session['id'],))
        user = cursor.fetchone()

        # ⚠️ SECURITY NOTE: ในแอปพลิเคชันจริง คุณต้องใช้ฟังก์ชันเปรียบเทียบแฮชที่ปลอดภัย (เช่น bcrypt.check_password_hash)
        if user and user['password'] == password:
            # 2. ยืนยันรหัสผ่านสำเร็จ -> ดำเนินการลบบัญชี
            
            # ลบข้อมูลผู้ใช้
            cursor.execute('DELETE FROM tbl_users WHERE id = %s', (session['id'],))
            
            # commit การเปลี่ยนแปลง
            conn.commit()
            
            # ล้าง session
            session.clear()
            
            return jsonify({'status': 'success', 'message': 'บัญชีของคุณถูกลบเรียบร้อยแล้ว'})
        else:
            # 3. รหัสผ่านไม่ถูกต้อง
            return jsonify({'status': 'error', 'message': 'รหัสผ่านไม่ถูกต้อง!'}), 403
            
    except mysql.connector.Error as err:
        # จัดการข้อผิดพลาดฐานข้อมูล
        return jsonify({'status': 'error', 'message': f'เกิดข้อผิดพลาดในการลบ: {err}'}), 500
        
    except Exception as e:
        # จัดการข้อผิดพลาดอื่น ๆ ที่ไม่คาดคิด
        return jsonify({'status': 'error', 'message': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {e}'}), 500
        
    finally:
        # ปิด cursor และ connection เสมอ
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        
@app.route('/logout')
def logout():
    """Logs out the current user by clearing session variables and updates online status.
    For 'viewer' role, deletes the temporary store and its data."""
    conn = get_db_connection()
    if conn and session.get('id'): # Only connect if session ID exists
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE tbl_users SET is_online = FALSE WHERE id = %s", (session['id'],))
            conn.commit()
        except mysql.connector.Error as err:
            print(f"Error updating online status on logout: {err}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # If the user was a viewer, delete their temporary store and all its data
    if session.get('role') == 'viewer' and session.get('store_id'):
        delete_viewer_store_and_data(session['store_id'])

    session.pop('loggedin', None)
    session.pop('id', None)
    session.pop('email', None)
    session.pop('firstname', None)
    session.pop('lastname', None)
    session.pop('role', None)
    session.pop('store_id', None) # Clear store_id from session
    session.pop('store_name', None) # Clear store_name from session
    flash('คุณได้ออกจากระบบแล้ว', 'info')
    return redirect(url_for('login'))

@app.route('/about')
def about():
    """Displays the 'About Us' page."""
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    """
    Handles the 'Contact Us' form submission.
    Currently, it just prints the form data and flashes a success message.
    """
    msg = ''
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        subject = request.form['subject']
        message = request.form['message']
        
        print(f"Contact Form: Name: {name}, Email: {email}, Subject: {subject}, Message: {message}")
        msg = 'ข้อความของคุณถูกส่งสำเร็จแล้ว!'
        flash(msg, 'success')
    return render_template('contact.html', msg=msg)

# --- Store Management (New) ---
@app.route("/tbl_stores", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator'])
def tbl_stores():
    """
    Manages stores.
    Supports adding, editing, deleting, and searching stores.
    """
    msg = ''
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("tbl_stores.html", stores=[], users=[])
    
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Fetch all users (especially moderators) for the moderator dropdown
        cursor.execute("SELECT id, CONCAT(firstname, ' ', lastname) as fullname, email, role FROM tbl_users WHERE role = 'moderator' OR role = 'administrator'")
        users = cursor.fetchall()

        if request.method == "POST":
            action = request.form.get('action')
            if action == 'add':
                store_name = request.form['store_name']
                address = request.form.get('address')
                phone = request.form.get('phone')
                moderator_user_id = request.form.get('moderator_user_id')
                
                try:
                    cursor.execute("INSERT INTO tbl_stores (store_name, address, phone, moderator_user_id) VALUES (%s, %s, %s, %s)",
                                   (store_name, address, phone, moderator_user_id))
                    conn.commit()
                    msg = 'เพิ่มร้านค้าสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการเพิ่มร้านค้า: {err}"
                    flash(msg, 'danger')
            elif action == 'edit':
                store_id = request.form['store_id']
                store_name = request.form['store_name']
                address = request.form.get('address')
                phone = request.form.get('phone')
                moderator_user_id = request.form.get('moderator_user_id')
                
                try:
                    cursor.execute("UPDATE tbl_stores SET store_name = %s, address = %s, phone = %s, moderator_user_id = %s WHERE store_id = %s",
                                   (store_name, address, phone, moderator_user_id, store_id))
                    conn.commit()
                    msg = 'อัปเดตร้านค้าสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการอัปเดต: {err}"
                    flash(msg, 'danger')
            elif action == 'delete':
                store_id = request.form['store_id']
                try:
                    # UPDATED: Set foreign keys to NULL in dependent tables before deleting store
                    cursor.execute("UPDATE tbl_category SET store_id = NULL WHERE store_id = %s", (store_id,))
                    cursor.execute("UPDATE tbl_products SET store_id = NULL WHERE store_id = %s", (store_id,))
                    cursor.execute("UPDATE tbl_order SET store_id = NULL WHERE store_id = %s", (store_id,))
                    cursor.execute("UPDATE tbl_users SET store_id = NULL WHERE store_id = %s", (store_id,))

                    cursor.execute("DELETE FROM tbl_stores WHERE store_id = %s", (store_id,))
                    conn.commit()
                    msg = 'ลบร้านค้าสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการลบร้านค้า: {err}"
                    flash(msg, 'danger')
            elif 'search' in request.form:
                search_query = request.form['search']
                cursor.execute("""
                    SELECT s.*, u.firstname, u.lastname, u.email as moderator_email
                    FROM tbl_stores s
                    LEFT JOIN tbl_users u ON s.moderator_user_id = u.id
                    WHERE s.store_name LIKE %s OR s.address LIKE %s OR s.phone LIKE %s OR u.email LIKE %s
                    ORDER BY s.store_id DESC
                """, ('%' + search_query + '%', '%' + search_query + '%', '%' + search_query + '%', '%' + search_query + '%'))
                stores = cursor.fetchall()
                return render_template("tbl_stores.html", stores=stores, users=users, search=search_query, msg=msg)

        # Fetch all stores for initial display
        cursor.execute("""
            SELECT s.*, u.firstname, u.lastname, u.email as moderator_email
            FROM tbl_stores s
            LEFT JOIN tbl_users u ON s.moderator_user_id = u.id
            ORDER BY s.store_id DESC
        """)
        stores = cursor.fetchall()
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลร้านค้า: {err}", 'danger')
        stores = [] # Set stores to empty in case of error
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return render_template("tbl_stores.html", stores=stores, users=users, search='', msg=msg)

# --- Category Management ---
@app.route("/tbl_category", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Allow viewer access
def tbl_category():
    """
    Manages product categories (e.g., PET, Aluminum, Glass, Burnable, Contaminated Waste).
    Supports adding, editing, deleting, and searching categories.
    Moderators/Members/Viewers can only manage categories for their assigned store.
    """
    msg = ''
    conn = get_db_connection()
    cursor = None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("tbl_category.html", categories=[], search='', stores=[])
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Fetch stores for dropdown (all for root_admin/administrator, only assigned for moderator/member/viewer)
        stores = []
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT store_id, store_name FROM tbl_stores ORDER BY store_name")
            stores = cursor.fetchall()
        elif session.get('role') in ['moderator', 'member', 'viewer']:
            if session.get('store_id'):
                cursor.execute("SELECT store_id, store_name FROM tbl_stores WHERE store_id = %s", (session['store_id'],))
                store_info = cursor.fetchone()
                if store_info:
                    stores = [store_info]


        if request.method == "POST":
            action = request.form.get('action')
            
            op_store_id = None
            if session.get('role') in ['root_admin', 'administrator']:
                op_store_id = request.form.get('store_id')
                if op_store_id == '':  # Handle case where admin selects "No Store"
                    op_store_id = None
            elif session.get('role') in ['moderator', 'member', 'viewer']:
                op_store_id = session.get('store_id')

            if not op_store_id and session.get('role') in ['moderator', 'member', 'viewer']:
                flash("คุณไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
                return render_template("tbl_category.html", categories=[], search='', stores=stores)

            if action == 'add':
                category_id = request.form['category_id']
                category_name = request.form['category_name']
                
                try:
                    cursor.execute("INSERT INTO tbl_category (category_id, category_name, store_id) VALUES (%s, %s, %s)", (category_id, category_name, op_store_id))
                    conn.commit()
                    msg = 'เพิ่มหมวดหมู่สำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการเพิ่มหมวดหมู่: {err}"
                    flash(msg, 'danger')
            elif action == 'edit':
                cat_db_id = request.form['cat_db_id']
                category_id = request.form['category_id']
                category_name = request.form['category_name']
                op_store_id = request.form.get('store_id')
                if op_store_id == '':
                    op_store_id = None

                if session.get('role') in ['moderator', 'member', 'viewer']:
                    cursor.execute("SELECT store_id FROM tbl_category WHERE id = %s", (cat_db_id,))
                    category_store_id_result = cursor.fetchone()
                    if not category_store_id_result or category_store_id_result['store_id'] != session['store_id']:
                        flash("คุณไม่มีสิทธิ์แก้ไขหมวดหมู่นี้", 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_category'))
                
                try:
                    # Update category with new values, including store_id
                    cursor.execute("UPDATE tbl_category SET category_id = %s, category_name = %s, store_id = %s WHERE id = %s", (category_id, category_name, op_store_id, cat_db_id))
                    conn.commit()
                    msg = 'อัปเดตหมวดหมู่สำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการอัปเดตหมวดหมู่: {err}"
                    flash(msg, 'danger')
            elif action == 'delete':
                cat_db_id = request.form['cat_db_id']
                
                if session.get('role') in ['moderator', 'member', 'viewer']:
                    cursor.execute("SELECT store_id FROM tbl_category WHERE id = %s", (cat_db_id,))
                    category_store_id_result = cursor.fetchone()
                    if not category_store_id_result or category_store_id_result['store_id'] != session['store_id']:
                        flash("คุณไม่มีสิทธิ์ลบหมวดหมู่นี้", 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_category'))

                try:
                    cursor.execute("UPDATE tbl_products SET category_id = NULL WHERE category_id = (SELECT category_id FROM tbl_category WHERE id = %s)", (cat_db_id,))
                    conn.commit()

                    cursor.execute("DELETE FROM tbl_category WHERE id = %s", (cat_db_id,))
                    conn.commit()
                    msg = 'ลบหมวดหมู่สำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการลบหมวดหมู่: {err}"
                    flash(msg, 'danger')
            
            elif 'search' in request.form:
                search_query = request.form['search']
                base_query = """
                    SELECT
                        c.id, c.category_id, c.category_name, c.store_id, s.store_name
                    FROM tbl_category c
                    LEFT JOIN tbl_stores s ON c.store_id = s.store_id
                    WHERE (c.category_name LIKE %s OR c.category_id LIKE %s)
                """
                query_params = ('%' + search_query + '%', '%' + search_query + '%')

                if session.get('role') in ['moderator', 'member', 'viewer']:
                    base_query += " AND c.store_id = %s"
                    query_params += (session.get('store_id'),)
                
                base_query += " ORDER BY c.id DESC"
                cursor.execute(base_query, query_params)
                categories = cursor.fetchall()
                return render_template("tbl_category.html", categories=categories, search=search_query, msg=msg, stores=stores)

        # Fetch all categories for initial display
        base_query = """
            SELECT
                c.id, c.category_id, c.category_name, c.store_id, s.store_name
            FROM tbl_category c
            LEFT JOIN tbl_stores s ON c.store_id = s.store_id
        """
        query_params = ()
        if session.get('role') in ['moderator', 'member', 'viewer']:
            base_query += " WHERE c.store_id = %s"
            query_params = (session.get('store_id'),)
        base_query += " ORDER BY c.id DESC"
        cursor.execute(base_query, query_params)
        categories = cursor.fetchall()

    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลหมวดหมู่: {err}", 'danger')
        categories = []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return render_template("tbl_category.html", categories=categories, search='', msg=msg, stores=stores)
@app.route("/tbl_products", methods=["GET", "POST"]) 
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Allow viewer access 
def tbl_products(): 
    """ 
    Manages products. 
    Supports adding, editing, deleting, and searching products. 
    Moderators/Members/Viewers can only manage products for their assigned store. 
    """ 
    msg = '' 
    conn = get_db_connection() 
    cursor = None # Initialize cursor to None 
    if not conn: 
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger') 
        return render_template("tbl_products.html", products=[], categories=[], search='', stores=[]) 
    try: 
        cursor = conn.cursor(dictionary=True) 
         
        # Fetch stores for dropdown (all for root_admin/administrator, only assigned for moderator/member/viewer) 
        stores = [] 
        if session.get('role') in ['root_admin', 'administrator']: 
            cursor.execute("SELECT store_id, store_name FROM tbl_stores ORDER BY store_name") 
            stores = cursor.fetchall() 
        elif session.get('role') in ['moderator', 'member', 'viewer']: 
            # Moderator/Member/Viewer can only see their own store 
            if session.get('store_id'): 
                cursor.execute("SELECT store_id, store_name FROM tbl_stores WHERE store_id = %s", (session['store_id'],)) 
                store_info = cursor.fetchone() 
                if store_info: 
                    stores = [store_info] 


        # Fetch all categories for the product dropdowns in modals (filtered by store_id for moderators/members/viewers) 
        category_base_query = "SELECT category_id, category_name FROM tbl_category" 
        category_query_params = () 
        if session.get('role') in ['moderator', 'member', 'viewer']: 
            category_base_query += " WHERE store_id = %s" 
            category_query_params = (session.get('store_id'),) 
        category_base_query += " ORDER BY category_name" 
        cursor.execute(category_base_query, category_query_params) 
        categories = cursor.fetchall() 

        if request.method == "POST": 
            action = request.form.get('action') 
             
            # Determine store_id for the operation based on user role 
            op_store_id = None 
            if session.get('role') in ['root_admin', 'administrator']: 
                op_store_id = request.form.get('store_id') 
            elif session.get('role') in ['moderator', 'member', 'viewer']: # Moderator/Member/Viewer restricted to their store 
                op_store_id = session.get('store_id') 

            if not op_store_id and session.get('role') in ['moderator', 'member', 'viewer']: # Ensure they have a store_id 
                flash("คุณไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger') 
                return render_template("tbl_products.html", products=[], categories=categories, search='', stores=stores) 

            if action == 'add': 
                products_id = request.form['products_id'] 
                product_name = request.form['product_name'] 
                # Convert stock and price to numbers, default to 0 if they are empty strings or None 
                stock = int(request.form['stock'] or 0) 
                price = float(request.form['price'] or 0.0) 
                category_id = request.form['category_id'] 
                description = request.form['description'] 
                 
                try: 
                    # Add store_id to product insertion 
                    # *** ลบ barcode_id ออกจาก INSERT Statement ***
                    cursor.execute("INSERT INTO tbl_products (products_id, products_name, stock, price, category_id, description, store_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",  
                                   (products_id, product_name, stock, price, category_id, description, op_store_id)) 
                    conn.commit() 
                    msg = 'เพิ่มสินค้าสำเร็จ!' 
                    flash(msg, 'success') 
                except mysql.connector.Error as err: 
                    msg = f"เกิดข้อผิดพลาดในการเพิ่มสินค้า: {err}" 
                    flash(msg, 'danger') 
            elif action == 'edit': 
                product_db_id = request.form['product_db_id'] # Use unique DB ID for update 
                products_id = request.form['products_id'] 
                product_name = request.form['product_name'] 
                # Convert stock and price to numbers, default to 0 if they are empty strings or None 
                stock = int(request.form['stock'] or 0) 
                price = float(request.form['price'] or 0.0) 
                category_id = request.form['category_id'] 
                description = request.form['description'] 
                 
                # Ensure moderator/member/viewer can only edit products within their store 
                if session.get('role') in ['moderator', 'member', 'viewer']: 
                    cursor.execute("SELECT store_id FROM tbl_products WHERE id = %s", (product_db_id,)) 
                    product_store_id_result = cursor.fetchone() 
                    if not product_store_id_result or product_store_id_result['store_id'] != session['store_id']: 
                        flash("คุณไม่มีสิทธิ์แก้ไขสินค้านี้", 'danger') 
                        conn.rollback() 
                        return redirect(url_for('tbl_products')) 

                try: 
                    # Update product with store_id included (though not changing it here) 
                    # *** ลบ barcode_id ออกจาก UPDATE Statement ***
                    cursor.execute("UPDATE tbl_products SET products_id = %s, products_name = %s, stock = %s, price = %s, category_id = %s, description = %s WHERE id = %s",  
                                   (products_id, product_name, stock, price, category_id, description, product_db_id)) 
                    conn.commit() 
                    msg = 'อัปเดตสินค้าสำเร็จ!' 
                    flash(msg, 'success') 
                except mysql.connector.Error as err: 
                    msg = f"เกิดข้อผิดพลาดในการอัปเดตสินค้า: {err}" 
                    flash(msg, 'danger') 
            elif action == 'delete': 
                product_db_id = request.form['product_db_id'] # Use unique DB ID for delete 
                 
                # Ensure moderator/member/viewer can only delete products within their store 
                if session.get('role') in ['moderator', 'member', 'viewer']: 
                    cursor.execute("SELECT store_id FROM tbl_products WHERE id = %s", (product_db_id,)) 
                    product_store_id_result = cursor.fetchone() 
                    if not product_store_id_result or product_store_id_result['store_id'] != session['store_id']: 
                        flash("คุณไม่มีสิทธิ์ลบสินค้านี้", 'danger') 
                        conn.rollback() 
                        return redirect(url_for('tbl_products')) 

                try: 
                    # UPDATED: Set foreign key in tbl_order to NULL first 
                    # Get the products_id corresponding to the product_db_id 
                    cursor.execute("SELECT products_id FROM tbl_products WHERE id = %s", (product_db_id,)) 
                    actual_products_id = cursor.fetchone()['products_id'] 

                    cursor.execute("UPDATE tbl_order SET products_id = NULL WHERE products_id = %s", (actual_products_id,)) 
                    conn.commit() # Commit update before delete 

                    cursor.execute("DELETE FROM tbl_products WHERE id = %s", (product_db_id,)) 
                    conn.commit() 
                    msg = 'ลบสินค้าสำเร็จ!' 
                    flash(msg, 'success') 
                except mysql.connector.Error as err: 
                    msg = f"เกิดข้อผิดพลาดในการลบสินค้า: {err}" 
                    flash(msg, 'danger') 
            elif 'search' in request.form: 
                search_query = request.form['search'] 
                base_query = """ 
                    SELECT p.*, c.category_name, s.store_name 
                    FROM tbl_products p 
                    LEFT JOIN tbl_category c ON p.category_id = c.category_id 
                    LEFT JOIN tbl_stores s ON p.store_id = s.store_id 
                    WHERE (p.products_name LIKE %s OR p.products_id LIKE %s OR c.category_name LIKE %s) 
                """ 
                query_params = ('%' + search_query + '%', '%' + search_query + '%', '%' + search_query + '%') 

                if session.get('role') in ['moderator', 'member', 'viewer']: # Filter by store for moderator/member/viewer 
                    base_query += " AND p.store_id = %s" 
                    query_params += (session.get('store_id'),) 
                 
                base_query += " ORDER BY p.id DESC" 
                cursor.execute(base_query, tuple(query_params)) 
                products = cursor.fetchall() 
                return render_template("tbl_products.html", products=products, categories=categories, search=search_query, msg=msg, stores=stores) 

        # Fetch all products for initial display (filtered by store_id for moderators/members/viewers) 
        base_query = """ 
            SELECT p.*, c.category_name, s.store_name 
            FROM tbl_products p 
            LEFT JOIN tbl_category c ON p.category_id = c.category_id 
            LEFT JOIN tbl_stores s ON p.store_id = s.store_id 
        """ 
        query_params = () 
        if session.get('role') in ['moderator', 'member', 'viewer']: 
            base_query += " WHERE p.store_id = %s" 
            query_params = (session.get('store_id'),) 
        base_query += " ORDER BY p.id DESC" 
        cursor.execute(base_query, query_params) 
        products = cursor.fetchall() 
    except mysql.connector.Error as err: 
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลสินค้า: {err}", 'danger') 
        products = [] # Set products to empty in case of error 
    finally: 
        if cursor: 
            cursor.close() 
        if conn: 
            conn.close() 
    return render_template("tbl_products.html", products=products, categories=categories, search='', msg=msg, stores=stores)

# --- Order Management ---
@app.route("/tbl_order", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def tbl_order():
    """
    Manages customer orders, including quantity tracking and disposed quantity.
    Supports adding, editing, deleting, and searching orders.
    Stock is updated based on ordered quantity.
    Moderators/Members/Viewers can only view/manage orders related to their store.
    """
    msg = ''
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("tbl_order.html", orders=[], products=[], users=[], search='', stores=[])
    try:
        cursor = conn.cursor(dictionary=True)
        
        # Fetch stores for dropdown (all for root_admin/administrator, only assigned for moderator/member/viewer)
        stores = []
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT store_id, store_name FROM tbl_stores ORDER BY store_name")
            stores = cursor.fetchall()
        elif session.get('role') in ['moderator', 'member', 'viewer']: # Viewer can also see their store
            # Moderator/Member/Viewer can only see their own store
            if session.get('store_id'):
                cursor.execute("SELECT store_id, store_name FROM tbl_stores WHERE store_id = %s", (session['store_id'],))
                store_info = cursor.fetchone()
                if store_info:
                    stores = [store_info]


        # Fetch all products for the product dropdowns in modals (filtered by store_id)
        product_base_query = "SELECT products_id, products_name, stock, price, barcode_id, store_id FROM tbl_products"
        product_query_params = ()
        if session.get('role') in ['moderator', 'member', 'viewer']:
            product_base_query += " WHERE store_id = %s"
            product_query_params = (session.get('store_id'),)
        product_base_query += " ORDER BY products_name"
        cursor.execute(product_base_query, product_query_params)
        products_data = cursor.fetchall()

        users_data = []
        # Fetch all users for the email dropdown in modals (filtered by store_id for moderators/members/viewers)
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT email, CONCAT(firstname, ' ', lastname) as fullname, store_id FROM tbl_users ORDER BY firstname")
            users_data = cursor.fetchall()
        elif session.get('role') in ['moderator', 'viewer']:
            cursor.execute("SELECT email, CONCAT(firstname, ' ', lastname) as fullname, store_id FROM tbl_users WHERE store_id = %s AND (role = 'member' OR role = 'viewer') ORDER BY firstname", (session.get('store_id'),))
            users_data = cursor.fetchall()
        elif session.get('role') == 'member': # Member can only select their own email
            users_data = [{'email': session['email'], 'fullname': f"{session['firstname']} {session['lastname']}"}]


        if request.method == "POST":
            action = request.form.get('action')
            
            # Determine store_id for the operation based on user role
            op_store_id = None
            if session.get('role') in ['root_admin', 'administrator']:
                op_store_id = request.form.get('store_id') # Admins can specify
            elif session.get('role') in ['moderator', 'member', 'viewer']:
                op_store_id = session.get('store_id') # Moderator/Member/Viewer restricted to their store

            if not op_store_id and session.get('role') in ['moderator', 'member', 'viewer']: # Ensure they have a store_id
                 flash("คุณไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
                 return render_template("tbl_order.html", orders=[], products=products_data, users=users_data, search='', stores=stores)

            if action == 'add':
                # Check permissions for adding orders
                # All roles except 'root_admin' and 'administrator' are checked here
                # Now 'viewer' is also allowed to add (to their temporary store)
                order_id = request.form['order_id']
                products_id = request.form['products_id']
                quantity = int(request.form['quantity'])
                disquantity = int(request.form['disquantity'])
                barcode_id = request.form['barcode_id'].strip() if request.form['barcode_id'] else None 
                
                # Determine order email based on user role and store_id
                email = request.form.get('email')
                if session.get('role') == 'member':
                    email = session['email']
                elif session.get('role') in ['moderator', 'viewer']: # Ensure moderator/viewer adds for members/viewers in their store
                    # Validate that the selected email belongs to a member/viewer of the current store
                    cursor.execute("SELECT id FROM tbl_users WHERE email = %s AND store_id = %s", (email, session['store_id']))
                    if not cursor.fetchone():
                        flash("ไม่สามารถเพิ่มคำสั่งซื้อ: อีเมลลูกค้าไม่อยู่ในร้านค้าของคุณ/จำลอง", 'danger')
                        return redirect(url_for('tbl_order'))
                elif not email:
                    flash("กรุณาระบุอีเมลลูกค้า.", 'danger')
                    return redirect(url_for('tbl_order'))

                # All roles allowed to modify data in their respective stores
                try: # Wrapped in an inner try-except for specific add errors
                    # Validate product existence, stock availability AND store_id
                    cursor.execute("SELECT products_name, stock, price, store_id FROM tbl_products WHERE products_id = %s", (products_id,))
                    product_info = cursor.fetchone()
                    
                    if not product_info:
                        msg = "ไม่พบสินค้า!"
                        flash(msg, 'danger')
                    # Ensure product_info['stock'] and product_info['price'] are treated as numbers
                    # Default to 0 if they are None (though DB schema has NOT NULL DEFAULT 0, defensive check)
                    product_stock = int(product_info['stock'] or 0)
                    product_price = float(product_info['price'] or 0.0)

                    # Admins are not restricted by store_id
                    if product_info['store_id'] != op_store_id and session.get('role') not in ['root_admin', 'administrator']: # Ensure product belongs to the current store context
                        msg = "สินค้าไม่ได้อยู่ในร้านค้าที่คุณเลือก/รับผิดชอบ!"
                        flash(msg, 'danger')
                    elif quantity > product_stock:
                        msg = f"สินค้า {product_info['products_name']} มีสต็อกไม่พอ. มีในสต็อก: {product_stock}"
                        flash(msg, 'danger')
                    else:
                        products_name = product_info['products_name']
                        
                        # Insert new order with user-provided disquantity, barcode_id, and store_id
                        cursor.execute("""
                            INSERT INTO tbl_order (order_id, products_id, products_name, quantity, disquantity, email, barcode_id, store_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (order_id, products_id, products_name, quantity, disquantity, email, barcode_id, op_store_id))
                        
                        # Update product stock (deduct ordered quantity)
                        cursor.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (quantity, products_id))
                        conn.commit()
                        msg = 'เพิ่มคำสั่งซื้อสำเร็จและอัปเดตสต็อกสินค้าแล้ว!'
                        flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการเพิ่มคำสั่งซื้อ: {err}"
                    flash(msg, 'danger')
            elif action == 'edit':
                ord_id = request.form['ord_id']
                order_id = request.form['order_id']
                products_id = request.form['products_id']
                quantity = int(request.form['quantity'])
                disquantity = int(request.form['disquantity']) 
                barcode_id = request.form['barcode_id'].strip() if request.form['barcode_id'] else None 
                email = request.form['email'] # Email from form, which might be read-only for members

                # Check permissions for editing
                # Root/Admin can edit any. Moderator/Member/Viewer can edit orders in their store.
                auth_ok = False
                if session.get('role') in ['root_admin', 'administrator']:
                    auth_ok = True
                else:
                    cursor.execute("SELECT store_id, email FROM tbl_order WHERE id = %s", (ord_id,))
                    order_ownership_info = cursor.fetchone()
                    if order_ownership_info and order_ownership_info['store_id'] == session.get('store_id'):
                        if session.get('role') in ['moderator', 'viewer']:
                            auth_ok = True
                        elif session.get('role') == 'member' and order_ownership_info['email'] == session.get('email'):
                            auth_ok = True
                
                if not auth_ok:
                    flash("คุณไม่มีสิทธิ์แก้ไขคำสั่งซื้อนี้", 'danger')
                    return redirect(url_for('tbl_order'))

                try:
                    # Get current order information to calculate stock change
                    cursor.execute("SELECT products_id, quantity, store_id FROM tbl_order WHERE id = %s", (ord_id,))
                    old_order_info = cursor.fetchone()

                    if not old_order_info:
                        msg = "ไม่พบคำสั่งซื้อที่ต้องการแก้ไข!"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))

                    # Admins are not restricted by store_id
                    if old_order_info['store_id'] != op_store_id and session.get('role') not in ['root_admin', 'administrator']:
                        flash("คุณไม่มีสิทธิ์แก้ไขคำสั่งซื้อนี้ เพราะไม่ได้อยู่ในร้านค้าของคุณ/ที่รับผิดชอบ", 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))

                    old_products_id = old_order_info['products_id']
                    old_quantity = old_order_info['quantity']

                    # Get new product information and its current stock
                    cursor.execute("SELECT products_name, stock, store_id FROM tbl_products WHERE products_id = %s", (products_id,))
                    new_product_info = cursor.fetchone()

                    if not new_product_info:
                        msg = "ไม่พบสินค้าใหม่ที่เลือก!"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))
                    
                    # Ensure new_product_info['stock'] is treated as a number
                    new_product_stock = int(new_product_info['stock'] or 0)

                    # Admins are not restricted by store_id
                    if new_product_info['store_id'] != op_store_id and session.get('role') not in ['root_admin', 'administrator']:
                        flash("สินค้าใหม่ที่เลือกไม่ได้อยู่ในร้านค้าที่คุณเลือก/รับผิดชอบ!", 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))

                    products_name = new_product_info['products_name']
                    
                    # --- Stock Adjustment Logic ---
                    if products_id != old_products_id:
                        # If product ID changes, restore old product's stock
                        cursor.execute("UPDATE tbl_products SET stock = stock + %s WHERE products_id = %s", (old_quantity, old_products_id))
                        
                        # Then deduct from the new product's stock
                        if quantity > new_product_stock: # Check if new quantity exceeds available stock
                            msg = f"สินค้า {products_name} มีสต็อกไม่พอสำหรับการสั่งซื้อใหม่. มีในสต็อก: {new_product_stock}"
                            flash(msg, 'danger')
                            conn.rollback() # Rollback if stock is insufficient
                            return redirect(url_for('tbl_order'))
                        cursor.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (quantity, products_id))
                    else:
                        # If product ID is the same, adjust stock based on the difference in quantity
                        quantity_difference = quantity - old_quantity
                        if new_product_stock - quantity_difference < 0: # Check if stock becomes negative
                            msg = f"สินค้า {products_name} มีสต็อกไม่พอสำหรับการเปลี่ยนแปลงจำนวน. มีในสต็อก: {new_product_stock}"
                            flash(msg, 'danger')
                            conn.rollback() # Rollback if stock is insufficient
                            return redirect(url_for('tbl_order'))
                        cursor.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (quantity_difference, products_id))
                    
                    # Update the order in tbl_order with new values, including disquantity, barcode_id, and store_id
                    cursor.execute("""
                        UPDATE tbl_order SET order_id = %s, products_id = %s, products_name = %s, quantity = %s, disquantity = %s, email = %s, barcode_id = %s, store_id = %s
                        WHERE id = %s
                    """, (order_id, products_id, products_name, quantity, disquantity, email, barcode_id, op_store_id, ord_id))
                    conn.commit()
                    msg = 'อัปเดตคำสั่งซื้อสำเร็จและอัปเดตสต็อกสินค้าแล้ว!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการอัปเดตคำสั่งซื้อ: {err}"
                    flash(msg, 'danger')
                    conn.rollback() # Rollback in case of error
            elif action == 'delete':
                ord_id = request.form['ord_id']
                order_email = request.form['email'] # Email from the form for permission check
                
                # Check permissions for deleting
                auth_ok = False
                if session.get('role') in ['root_admin', 'administrator']:
                    auth_ok = True
                else:
                    cursor.execute("SELECT store_id, email FROM tbl_order WHERE id = %s", (ord_id,))
                    order_ownership_info = cursor.fetchone()
                    if order_ownership_info and order_ownership_info['store_id'] == session.get('store_id'):
                        if session.get('role') in ['moderator', 'viewer']:
                            auth_ok = True
                        elif session.get('role') == 'member' and order_ownership_info['email'] == session.get('email'):
                            auth_ok = True
                
                if not auth_ok:
                    flash("คุณไม่มีสิทธิ์ลบคำสั่งซื้อนี้", 'danger')
                    return redirect(url_for('tbl_order'))

                try:
                    # Get order information before deleting to restore stock
                    cursor.execute("SELECT products_id, quantity, store_id FROM tbl_order WHERE id = %s", (ord_id,))
                    order_to_delete = cursor.fetchone()

                    if not order_to_delete:
                        msg = "ไม่พบคำสั่งซื้อที่ต้องการลบ!"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))
                    
                    # Admins are not restricted by store_id
                    if order_to_delete['store_id'] != op_store_id and session.get('role') not in ['root_admin', 'administrator']:
                        flash("คุณไม่มีสิทธิ์ลบคำสั่งซื้อนี้ เพราะไม่ได้อยู่ในร้านค้าของคุณ/ที่รับผิดชอบ", 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_order'))

                    product_id_to_restore = order_to_delete['products_id']
                    quantity_to_restore = order_to_delete['quantity']

                    # Delete the order from tbl_order
                    cursor.execute("DELETE FROM tbl_order WHERE id = %s", (ord_id,))
                    # Restore product stock in tbl_products (based on original ordered quantity)
                    cursor.execute("UPDATE tbl_products SET stock = stock + %s WHERE products_id = %s", (quantity_to_restore, product_id_to_restore))
                    conn.commit()
                    msg = 'ลบคำสั่งซื้อสำเร็จและคืนสต็อกสินค้าแล้ว!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการลบคำสั่งซื้อ: {err}"
                    flash(msg, 'danger')
                    conn.rollback() # Rollback in case of error
            elif 'search' in request.form:
                search_query = request.form['search']
                query_params = ['%' + search_query + '%'] * 3
                
                base_query = """
                    SELECT 
                        o.*, 
                        p.category_id,
                        p.price,
                        s.store_name -- Added store_name from tbl_stores
                    FROM tbl_order o
                    LEFT JOIN tbl_products p ON o.products_id = p.products_id
                    LEFT JOIN tbl_stores s ON o.store_id = s.store_id
                    WHERE (o.order_id LIKE %s OR o.products_name LIKE %s OR o.email LIKE %s)
                """
                if session.get('role') == 'member':
                    base_query += " AND o.email = %s AND o.store_id = %s"
                    query_params.append(session['email'])
                    query_params.append(session['store_id'])
                elif session.get('role') in ['moderator', 'viewer']: # Viewer now acts like moderator
                    base_query += " AND o.store_id = %s"
                    query_params.append(session['store_id'])

                base_query += " ORDER BY o.id DESC"
                cursor.execute(base_query, tuple(query_params))
                orders_raw = cursor.fetchall()

                orders = []
                for order_raw in orders_raw:
                    order = order_raw.copy()
                    order['price'] = float(order['price'] or 0.0) # Ensure price is float
                    orders.append(order)

                return render_template("tbl_order.html", orders=orders, products=products_data, users=users_data, search=search_query, msg=msg, stores=stores)

        # SQL query for initial display of orders, explicitly selecting barcode_id, price, and store_name
        base_query = """
            SELECT 
                o.*, 
                p.category_id,
                p.price,
                s.store_name
            FROM tbl_order o
            LEFT JOIN tbl_products p ON o.products_id = p.products_id
            LEFT JOIN tbl_stores s ON o.store_id = s.store_id
        """
        query_params = ()
        if session.get('role') in ['moderator', 'member', 'viewer']: # For moderator/member/viewer, filter by their store
            base_query += " WHERE o.store_id = %s"
            query_params = (session.get('store_id'),)

        base_query += " ORDER BY o.id DESC"
        cursor.execute(base_query, query_params)
        orders_raw = cursor.fetchall()

        # Ensure price is converted to float for display
        orders = []
        for order_raw in orders_raw:
            order = order_raw.copy()
            order['price'] = float(order['price'] or 0.0) # Handle NoneType for price here for display
            orders.append(order)
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลคำสั่งซื้อ: {err}", 'danger')
        orders = [] # Set orders to empty in case of error
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return render_template("tbl_order.html", orders=orders, products=products_data, users=users_data, search='', msg=msg, stores=stores)

# --- User Management ---
@app.route("/tbl_users", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member']) # Viewer cannot access this page
def tbl_users():
    """
    Manages user accounts and their roles (root_admin, administrator, moderator, member, viewer).
    Supports adding, searching, editing, and deleting users.
    Root admin cannot be deleted. Administrators cannot create/edit root_admin users.
    Moderators/Members can only manage 'member' and 'viewer' roles within their assigned store.
    """
    msg = ''
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return render_template("tbl_users.html", users=[], search='', stores=[])
    try:
        cursor = conn.cursor(dictionary=True)
        root_admin_id = None
        try:
            cursor.execute("SELECT id FROM tbl_users WHERE role = 'root_admin' LIMIT 1")
            result = cursor.fetchone()
            if result:
                root_admin_id = result['id']
        except mysql.connector.Error as err:
            print(f"Error fetching root_admin_id: {err}")

        # Fetch stores for dropdown (all for root_admin/administrator, only assigned for moderator/member)
        stores = []
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT store_id, store_name FROM tbl_stores ORDER BY store_name")
            stores = cursor.fetchall()
        elif session.get('role') in ['moderator', 'member']: # Member can also see their store
            if session.get('store_id'):
                cursor.execute("SELECT store_id, store_name FROM tbl_stores WHERE store_id = %s", (session['store_id'],))
                store_info = cursor.fetchone()
                if store_info:
                    stores = [store_info]


        if request.method == "POST":
            action = request.form.get('action')
            
            # Determine store_id for the operation based on user role
            op_store_id = None
            if session.get('role') in ['root_admin', 'administrator']:
                op_store_id = request.form.get('store_id')
            elif session.get('role') in ['moderator', 'member']: # Member can also manage users in their store
                # Moderator/Member can only manage users within their own store
                op_store_id = session.get('store_id')
                if not op_store_id: # Should ideally be caught by role_required if moderator exists without store_id
                    flash("คุณไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
                    return render_template("tbl_users.html", users=[], search='', stores=stores)


            if action == 'add':
                firstname = request.form.get('firstname')
                lastname = request.form.get('lastname')
                email = request.form.get('email')
                password = request.form.get('password')
                role = request.form.get('role')
                
                # Permission check for adding users
                can_add = False
                if session.get('role') == 'root_admin':
                    can_add = True # Root admin can add any role
                elif session.get('role') == 'administrator':
                    if role not in ['root_admin']: # Admin cannot add root_admin
                        can_add = True
                elif session.get('role') == 'moderator':
                    # Moderator can only add member/viewer and they are automatically assigned to moderator's store
                    if role in ['member', 'viewer']:
                        can_add = True
                    else:
                        msg = "Moderator สามารถเพิ่มได้เฉพาะ Member หรือ Viewer เท่านั้น"
                        flash(msg, 'danger')
                        return redirect(url_for('tbl_users'))
                elif session.get('role') == 'member': # Member can add member/viewer roles to their store
                    if role in ['member', 'viewer']:
                        can_add = True
                    else:
                        msg = "Member สามารถเพิ่มได้เฉพาะ Member หรือ Viewer เท่านั้น"
                        flash(msg, 'danger')
                        return redirect(url_for('tbl_users'))


                if not can_add:
                    msg = "คุณไม่มีสิทธิ์เพิ่มผู้ใช้งานด้วยยศนี้"
                    flash(msg, 'danger')
                    return redirect(url_for('tbl_users'))
                
                try:
                    # Add store_id to user insertion, new user is offline by default
                    cursor.execute('INSERT INTO tbl_users (firstname, lastname, email, password, role, store_id, is_online) VALUES (%s, %s, %s, %s, %s, %s, FALSE)', (firstname, lastname, email, password, role, op_store_id))
                    conn.commit()
                    msg = 'เพิ่มผู้ใช้งานสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการเพิ่มผู้ใช้งาน: {err}"
                    flash(msg, 'danger')
            elif action == 'edit':
                user_id = request.form.get('user_id')
                firstname = request.form.get('firstname')
                lastname = request.form.get('lastname')
                email = request.form.get('email')
                password = request.form.get('password') if request.form.get('password') else None
                role = request.form.get('role')
                
                cursor.execute("SELECT role, store_id, email FROM tbl_users WHERE id = %s", (user_id,)) # Fetch current email too
                target_user_info = cursor.fetchone()
                if not target_user_info:
                    flash("ไม่พบผู้ใช้งานที่ต้องการแก้ไข.", 'danger')
                    return redirect(url_for('tbl_users'))
                target_user_role = target_user_info['role']
                target_user_store_id = target_user_info['store_id']
                old_email = target_user_info['email']

                # Permission checks for editing users
                can_edit = False
                if session.get('role') == 'root_admin':
                    can_edit = True
                    # Root admin cannot change their own role to non-root admin
                    if target_user_role == 'root_admin' and role != 'root_admin' and str(user_id) == str(session['id']):
                        msg = "คุณไม่สามารถเปลี่ยนยศของ Root Admin ที่เข้าสู่ระบบอยู่ได้"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_users'))
                elif session.get('role') == 'administrator':
                    if target_user_role == 'root_admin' or role == 'root_admin':
                        msg = "คุณไม่มีสิทธิ์แก้ไขผู้ใช้งาน Root Admin หรือกำหนดให้เป็น Root Admin"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_users'))
                    can_edit = True
                elif session.get('role') in ['moderator', 'member']: # Moderator/Member can edit users in their store
                    # Moderator/Member can only edit users within their store and with 'member' or 'viewer' roles
                    # Also, the new role must be 'member' or 'viewer'
                    if (target_user_store_id == session.get('store_id') and
                        target_user_role in ['member', 'viewer'] and
                        target_user_role in ['member', 'viewer']): # <--- เงื่อนไขนี้ที่ทำให้ลบได้
                        can_edit = True
                    else:
                        msg = "คุณสามารถแก้ไขได้เฉพาะ Member หรือ Viewer ในร้านค้าของตัวเองเท่านั้น"
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_users'))

                if not can_edit:
                    msg = "คุณไม่มีสิทธิ์แก้ไขผู้ใช้งานนี้"
                    flash(msg, 'danger')
                    conn.rollback()
                    return redirect(url_for('tbl_users'))

                try:
                    # Check for duplicate email (excluding current user)
                    cursor.execute('SELECT id FROM tbl_users WHERE email = %s AND id != %s', (email, user_id,))
                    existing_email = cursor.fetchone()
                    if existing_email:
                        msg = 'อีเมลนี้มีผู้ใช้งานอื่นแล้ว!'
                        flash(msg, 'danger')
                        conn.rollback()
                        return redirect(url_for('tbl_users'))
                    
                    # UPDATED: If email changes, update tbl_order
                    if email != old_email:
                        cursor.execute("UPDATE tbl_order SET email = %s WHERE email = %s", (email, old_email,))
                        conn.commit() # Commit this update immediately

                    if password:
                        cursor.execute('UPDATE tbl_users SET firstname = %s, lastname = %s, email = %s, password = %s, role = %s, store_id = %s WHERE id = %s', 
                                        (firstname, lastname, email, password, role, op_store_id, user_id)) # Update store_id
                    else:
                        cursor.execute('UPDATE tbl_users SET firstname = %s, lastname = %s, email = %s, role = %s, store_id = %s WHERE id = %s', 
                                        (firstname, lastname, email, role, op_store_id, user_id)) # Update store_id
                    conn.commit()
                    msg = 'อัปเดตผู้ใช้งานสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการอัปเดตผู้ใช้งาน: {err}"
                    flash(msg, 'danger')
                    conn.rollback()
            elif action == 'delete':
                user_id = request.form.get('user_id')
                
                # Prevent deleting the currently logged-in user
                if str(user_id) == str(session['id']):
                    msg = "คุณไม่สามารถลบบัญชีผู้ใช้ของคุณเองได้!"
                    flash(msg, 'danger')
                    return redirect(url_for('tbl_users'))

                cursor.execute("SELECT role, store_id, email FROM tbl_users WHERE id = %s", (user_id,))
                target_user_info = cursor.fetchone()
                if not target_user_info:
                    flash("ไม่พบผู้ใช้งานที่ต้องการลบ.", 'danger')
                    return redirect(url_for('tbl_users'))
                target_user_role = target_user_info['role']
                target_user_store_id = target_user_info['store_id']
                target_user_email = target_user_info['email']

                # Permission checks for deleting users
                can_delete = False
                if session.get('role') == 'root_admin':
                    can_delete = True
                elif session.get('role') == 'administrator':
                    if target_user_role not in ['root_admin', 'administrator']: # Admin can delete moderator, member, viewer
                        can_delete = True
                elif session.get('role') in ['moderator', 'member']: # Moderator/Member can delete users in their store
                    # Moderator/Member can only delete 'member' or 'viewer' roles within their assigned store
                    if (target_user_store_id == session.get('store_id') and
                        target_user_role in ['member', 'viewer']):
                        can_delete = True
                    else:
                        msg = "คุณสามารถลบได้เฉพาะ Member หรือ Viewer ในร้านค้าของตัวเองเท่านั้น"
                        flash(msg, 'danger')
                        return redirect(url_for('tbl_users'))
                
                # Specific checks for root_admin
                if target_user_role == 'root_admin':
                    cursor.execute("SELECT COUNT(*) FROM tbl_users WHERE role = 'root_admin'")
                    root_admin_count = cursor.fetchone()[0]
                    if root_admin_count <= 1: # Prevent deleting the last root_admin
                        msg = "ไม่สามารถลบ Root Admin คนสุดท้ายได้!"
                        flash(msg, 'danger')
                        return redirect(url_for('tbl_users'))
                    if session.get('role') != 'root_admin':
                        msg = "คุณไม่มีสิทธิ์ลบผู้ใช้งาน Root Admin"
                        flash(msg, 'danger')
                        return redirect(url_for('tbl_users'))
                
                if not can_delete:
                    msg = "คุณไม่มีสิทธิ์ลบผู้ใช้งานนี้"
                    flash(msg, 'danger')
                    return redirect(url_for('tbl_users'))

                try:
                    # UPDATED: Set foreign keys to NULL in dependent tables before deleting user
                    cursor.execute("UPDATE tbl_order SET email = NULL WHERE email = %s", (target_user_email,))
                    cursor.execute("UPDATE tbl_stores SET moderator_user_id = NULL WHERE moderator_user_id = %s", (user_id,))
                    conn.commit() # Commit updates before delete

                    cursor.execute("DELETE FROM tbl_users WHERE id = %s", (user_id,))
                    conn.commit()
                    msg = 'ลบผู้ใช้งานสำเร็จ!'
                    flash(msg, 'success')
                except mysql.connector.Error as err:
                    msg = f"เกิดข้อผิดพลาดในการลบผู้ใช้งาน: {err}"
                    flash(msg, 'danger')
            elif 'search' in request.form:
                search_query = request.form.get('search')
                base_query = "SELECT u.*, s.store_name FROM tbl_users u LEFT JOIN tbl_stores s ON u.store_id = s.store_id WHERE (u.firstname LIKE %s OR u.lastname LIKE %s OR u.email LIKE %s OR u.role LIKE %s)"
                query_params = ('%' + search_query + '%', '%' + search_query + '%', '%' + search_query + '%', '%' + search_query + '%')

                if session.get('role') in ['moderator', 'member']: # Filter by store for moderator/member
                    base_query += " AND u.store_id = %s"
                    query_params += (session.get('store_id'),)
                
                base_query += " ORDER BY u.id DESC"
                cursor.execute(base_query, query_params)
                users = cursor.fetchall()
                return render_template("tbl_users.html", users=users, search=search_query, msg=msg, stores=stores)

        # Fetch all users for initial display (filtered by store_id for moderators/members)
        base_query = "SELECT u.*, s.store_name FROM tbl_users u LEFT JOIN tbl_stores s ON u.store_id = s.store_id"
        query_params = ()
        if session.get('role') in ['moderator', 'member']:
            base_query += " WHERE u.store_id = %s"
            query_params = (session.get('store_id'),)
        base_query += " ORDER BY u.id DESC"
        cursor.execute(base_query, query_params)
        users = cursor.fetchall()
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลผู้ใช้งาน: {err}", 'danger')
        users = [] # Set users to empty in case of error
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return render_template("tbl_users.html", users=users, search='', msg=msg, stores=stores)
# --- Report Generation ---

@app.route("/export_products_csv")
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Allow viewer to export
def export_products_csv():
    """Exports product data to a CSV file."""
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('tbl_products'))
    
    try:
        cursor = conn.cursor(dictionary=True)
        base_query = "SELECT products_id, products_name, stock, price, category_id, description, barcode_id, store_id FROM tbl_products"
        query_params = ()
        if session.get('role') in ['moderator', 'member', 'viewer']:
            base_query += " WHERE store_id = %s"
            query_params = (session.get('store_id'),)

        cursor.execute(base_query, query_params)
        products = cursor.fetchall()
        
        si = StringIO()
        cw = csv.writer(si)
        
        cw.writerow(['Product ID', 'Product Name', 'Stock', 'Price', 'Category ID', 'Description', 'Barcode ID', 'Store ID'])
        
        for product in products:
            # Ensure stock and price are handled as numbers, defaulting to 0 if None
            display_stock = product['stock'] if product['stock'] is not None else 0
            display_price = product['price'] if product['price'] is not None else 0.0
            cw.writerow([product['products_id'], product['products_name'], display_stock, display_price, product['category_id'], product['description'], product['barcode_id'], product['store_id']])
        
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = "attachment; filename=products_report.csv"
        output.headers["Content-type"] = "text/csv"
        return output
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการส่งออกข้อมูลสินค้า: {err}", 'danger')
        return redirect(url_for('tbl_products'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- Route จัดการคำสั่งซื้อ (cart) ---
@app.route("/cart", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Allow viewer access
def cart():
    """ 
    Handles the shopping cart functionality, including adding products,
    completing an order, and displaying the current order items.
    Users can only add products from their assigned store.
    Order IDs are generated based on the latest order_id for the specific store.
    Viewers can access and persist data to their temporary store.
    """
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    msg = ''
    pre_filled_products_id_input = ''
    selected_product_details_display = 'จะแสดงที่นี่หลังจากระบุรหัสสินค้า'
    selected_product_barcode = '' # Initialize with empty string

    # Determine store_id for current operations
    current_user_store_id = session.get('store_id')
    # This check ensures that even viewers (who get a store_id on login) have one
    if not current_user_store_id and session.get('role') in ['moderator', 'member', 'viewer']:
        flash("คุณยังไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
        return redirect(url_for('index'))

    try:
        cursor = conn.cursor(dictionary=True)
        # Fetch stores for dropdown (all for root_admin/administrator, only assigned for moderator/member/viewer)
        stores = []
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT store_id, store_name FROM tbl_stores ORDER BY store_name")
            stores = cursor.fetchall()
        elif session.get('role') in ['moderator', 'member', 'viewer']:
            if session.get('store_id'):
                cursor.execute("SELECT store_id, store_name FROM tbl_stores WHERE store_id = %s", (session['store_id'],))
                store_info = cursor.fetchone()
                if store_info:
                    stores = [store_info]


        # --- Logic for creating/managing current_order_id and barcode_id for the order ---
        # The current_order_id should be unique PER STORE
        current_order_id_key = f'current_order_id_{current_user_store_id}'
        current_order_barcode_key = f'current_order_barcode_{current_user_store_id}'

        current_order_id = session.get(current_order_id_key)
        # Fix: Use current_order_barcode_key to retrieve the barcode associated with the current order.
        selected_product_barcode = session.get(current_order_barcode_key)

        if not current_order_id:
            # Fetch max order_id for the specific store_id
            cursor.execute("SELECT MAX(CAST(order_id AS UNSIGNED)) AS max_order_id FROM tbl_order WHERE order_id REGEXP '^[0-9]+$' AND store_id = %s", (current_user_store_id,))
            result = cursor.fetchone()
            current_order_id = str(int(result['max_order_id'] or 0) + 1) if result and result['max_order_id'] is not None else '100001' # Handles first order for a new store
            session[current_order_id_key] = current_order_id

            # Generate a new unique barcode_id for this order
            # This barcode needs to be unique globally, even for viewer's temporary stores
            is_unique = False
            attempts = 0
            max_attempts = 5000
            cursor.execute("SELECT barcode_id FROM tbl_order WHERE barcode_id IS NOT NULL AND barcode_id != ''")
            existing_barcode_ids_in_db = {item['barcode_id'] for item in cursor.fetchall()}
            while not is_unique and attempts < max_attempts:
                seed = random.randint(10**11, 10**12 - 1)
                encoded_barcode_int = encode(seed)
                new_barcode = str(encoded_barcode_int).zfill(13)
                if new_barcode not in existing_barcode_ids_in_db:
                    is_unique = True
                    selected_product_barcode = new_barcode # Update the selected_product_barcode for this session
                    session[current_order_barcode_key] = new_barcode # Fixed: use new_barcode, not selected_barcode
                attempts += 1
            if not is_unique:
                flash("ไม่สามารถสร้างบาร์โค้ดที่ไม่ซ้ำกันสำหรับคำสั่งซื้อใหม่ได้. โปรดลองอีกครั้ง.", 'warning')
                return redirect(url_for('cart'))

        # Fetch all product data and user data for the frontend (filtered by store_id)
        # Ensure stock and price are converted to numbers or default to 0 if None
        cursor.execute("SELECT products_id, products_name, stock, price, barcode_id FROM tbl_products WHERE store_id = %s ORDER BY products_id", (current_user_store_id,))
        products_data_raw = cursor.fetchall()
        products_data = []
        for p_raw in products_data_raw:
            p = p_raw.copy()
            p['stock'] = int(p['stock'] or 0) # Convert stock to int, default 0
            p['price'] = float(p['price'] or 0.0) # Convert price to float, default 0.0
            products_data.append(p)

        products_data_parts = [f"{p['products_id']}|{str(p['products_name']).replace('|', ' ').replace('///', ' ')}|{p['stock']}|{p['price']}|{str(p['barcode_id'] or '').replace('|', ' ').replace('///', ' ')}" for p in products_data]
        products_data_string = "///".join(products_data_parts)
        
        users_data = []
        # Filter users for the dropdown based on the current store (Viewer can select any member/viewer in their temp store)
        if session.get('role') in ['root_admin', 'administrator']:
            cursor.execute("SELECT email, CONCAT(firstname, ' ', lastname) as fullname, store_id FROM tbl_users WHERE store_id = %s ORDER BY firstname", (current_user_store_id,))
        elif session.get('role') in ['moderator', 'viewer']:
            cursor.execute("SELECT email, CONCAT(firstname, ' ', lastname) as fullname, store_id FROM tbl_users WHERE store_id = %s AND (role = 'member' OR role = 'viewer') ORDER BY firstname", (current_user_store_id,))
        elif session.get('role') == 'member':
            cursor.execute("SELECT email, CONCAT(firstname, ' ', lastname) as fullname FROM tbl_users WHERE id = %s AND store_id = %s", (session['id'], current_user_store_id,))
        users_data = cursor.fetchall()

        # --- Handle 'complete_order' action ---
        if request.method == "POST" and request.form.get('action') == 'complete_order':
            # Viewers can complete orders, as the data is stored to their temporary store
            
            # Fetch all items in the current order for this store
            cursor.execute("""
                SELECT o.*, p.price
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.order_id = %s AND o.store_id = %s
                ORDER BY o.id
            """, (current_order_id, current_user_store_id))
            orders_to_complete = cursor.fetchall()
            
            if not orders_to_complete:
                flash('ไม่พบรายการสินค้าในคำสั่งซื้อนี้.', 'danger')
                return redirect(url_for('cart'))

            total_quantity = sum(item['quantity'] for item in orders_to_complete)
            # Ensure price is converted to float before multiplication
            total_price = sum(item['quantity'] * float(item['price'] or 0.0) for item in orders_to_complete)
            
            # Store receipt data in session and clear current order info
            session['receipt_data'] = {
                'orders': orders_to_complete,
                'barcode_id': selected_product_barcode, # Use selected_product_barcode here
                'total_quantity': total_quantity,
                'total_price': total_price,
                'current_order_id': current_order_id,
                'store_id': current_user_store_id
            }
            session.pop(current_order_id_key, None)
            session.pop(current_order_barcode_key, None)
            
            flash('คำสั่งซื้อเสร็จสมบูรณ์แล้ว!', 'success')
            return redirect(url_for('receipt_display'))

        # --- Main logic for adding item automatically when products_id_input length is 13 ---
        if request.method == "POST" and 'products_id_input' in request.form:
            products_id_input = request.form.get('products_id_input')
            pre_filled_products_id_input = products_id_input
            
            if len(products_id_input) == 13:
                product_info = None
                
                # Search for the product by products_id and ensure it belongs to the current store
                cursor.execute("SELECT products_id, products_name, stock, price, store_id FROM tbl_products WHERE products_id = %s AND store_id = %s", (products_id_input, current_user_store_id))
                product_info_raw = cursor.fetchone()
                
                if not product_info_raw:
                    flash("ไม่พบสินค้าตามบาร์โค้ดที่ระบุในร้านค้าของคุณ!", 'danger')
                    return redirect(url_for('cart'))
                
                product_info = product_info_raw.copy()
                # Ensure product_info['stock'] is treated as a number
                product_info['stock'] = int(product_info['stock'] or 0)
                product_info['price'] = float(product_info['price'] or 0.0) # Ensure price is float

                # --- Proceed to add the product to the cart ---
                order_id_to_use = session.get(current_order_id_key)
                barcode_to_use_for_add = session.get(current_order_barcode_key)

                if not order_id_to_use or not barcode_to_use_for_add:
                    flash("ไม่สามารถดำเนินการได้: รหัสคำสั่งซื้อหรือบาร์โค้ดคำสั่งซื้อปัจจุบันไม่พร้อมใช้งาน.", 'danger')
                    return redirect(url_for('cart'))
                
                email = request.form.get('email')
                if session.get('role') == 'member':
                    email = session['email']
                elif session.get('role') in ['moderator', 'viewer']:
                    # Validate that the selected email belongs to a member/viewer of the current store
                    cursor.execute("SELECT id FROM tbl_users WHERE email = %s AND store_id = %s", (email, session['store_id']))
                    if not cursor.fetchone():
                        flash("ไม่สามารถเพิ่มคำสั่งซื้อ: อีเมลลูกค้าไม่อยู่ในร้านค้าของคุณ/จำลอง", 'danger')
                        return redirect(url_for('cart'))
                elif not email:
                    flash("กรุณาระบุอีเมลลูกค้า.", 'danger')
                    return redirect(url_for('cart'))

                # All roles allowed to modify data in their respective stores
                quantity = 1
                disquantity = 0
                if quantity > product_info['stock']:
                    flash(f"สินค้า {product_info['products_name']} มีสต็อกไม่พอ. มีในสต็อก: {product_info['stock']}", 'danger')
                    return redirect(url_for('cart'))
                
                products_name = product_info['products_name']
                products_id_to_use = product_info['products_id']

                cursor.execute("SELECT id, quantity FROM tbl_order WHERE products_id = %s AND order_id = %s AND email = %s AND store_id = %s",
                               (products_id_to_use, order_id_to_use, email, current_user_store_id))
                existing_order_item = cursor.fetchone()

                if existing_order_item:
                    new_qty = existing_order_item['quantity'] + quantity
                    if new_qty > product_info['stock']:
                        flash(f"ไม่สามารถเพิ่มได้ สินค้า {products_name} มีสต็อกไม่พอสำหรับยอดรวม. มีในสต็อก: {product_info['stock']}", 'danger')
                    else:
                        cursor.execute("UPDATE tbl_order SET quantity = %s WHERE id = %s", (new_qty, existing_order_item['id']))
                        cursor.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (quantity, products_id_to_use))
                        conn.commit()
                        flash(f'เพิ่มจำนวนสินค้า {products_name} ในรายการสั่งซื้อ {order_id_to_use} สำเร็จ และอัปเดตสต็อกแล้ว!', 'success')
                else:
                    cursor.execute("""
                        INSERT INTO tbl_order (order_id, products_id, products_name, quantity, disquantity, email, barcode_id, store_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (order_id_to_use, products_id_to_use, products_name, quantity, disquantity, email, barcode_to_use_for_add, current_user_store_id))
                    cursor.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (quantity, products_id_to_use))
                    conn.commit()
                    flash('เพิ่มคำสั่งซื้อสำเร็จและอัปเดตสต็อกสินค้าแล้ว!', 'success')
                return redirect(url_for('cart'))
        
        # --- For GET Request and final display ---
        orders_data = []
        base_order_query = """
            SELECT o.*, p.price, p.products_name AS product_name_from_db, s.store_name
            FROM tbl_order o
            LEFT JOIN tbl_products p ON o.products_id = p.products_id
            LEFT JOIN tbl_stores s ON o.store_id = s.store_id
            WHERE o.order_id = %s AND o.store_id = %s
        """
        query_params = (current_order_id, current_user_store_id)

        if session.get('role') == 'member':
            base_order_query += " AND o.email = %s"
            query_params += (session['email'],)
        
        base_order_query += " ORDER BY o.id DESC"
        cursor.execute(base_order_query, query_params)
        orders_data_raw = cursor.fetchall()
        
        # Ensure price is converted to float for display in orders_data
        orders_data = []
        for o_raw in orders_data_raw:
            o = o_raw.copy()
            o['price'] = float(o['price'] or 0.0) # Convert price to float, default 0.0
            orders_data.append(o)

    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลคำสั่งซื้อ: {err}", 'danger')
        orders_data = []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    # If the user submitted a form and the page is being re-rendered, update the product display.
    if request.method == 'POST' and pre_filled_products_id_input:
        conn_display_update = get_db_connection() # Re-open connection for this
        cursor_display_update = None
        if conn_display_update:
            try:
                cursor_display_update = conn_display_update.cursor(dictionary=True)
                cursor_display_update.execute("SELECT products_id, products_name, stock, price, barcode_id FROM tbl_products WHERE products_id = %s AND store_id = %s", (pre_filled_products_id_input, current_user_store_id))
                found_product_raw = cursor_display_update.fetchone()
                if found_product_raw:
                    found_product = found_product_raw.copy()
                    found_product['stock'] = int(found_product['stock'] or 0) # Convert stock to int, default 0
                    selected_product_details_display = f"{found_product['products_name']} | สต็อก: {found_product['stock']}"
                else:
                    selected_product_details_display = 'ไม่พบสินค้าที่ระบุ'
            except mysql.connector.Error as err:
                print(f"Error fetching product for display update: {err}")
                selected_product_details_display = 'เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล'
            finally:
                if cursor_display_update:
                    cursor_display_update.close()
                if conn_display_update:
                    conn_display_update.close()
        else:
            selected_product_details_display = 'เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล'

    return render_template("cart.html",
                           orders=orders_data,
                           products_data_string=products_data_string,
                           users=users_data,
                           search='',
                           msg=msg,
                           current_auto_order_id=current_order_id,
                           request_form_data=request.form if request.method == 'POST' else {},
                           selected_product_details_display=selected_product_details_display,
                           selected_product_barcode=selected_product_barcode or '', # Use the specific barcode for the current order
                           pre_filled_products_id_input=pre_filled_products_id_input,
                           stores=stores)


# --- New route to display the PNG receipt ---
@app.route("/receipt_display")
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def receipt_display():
    """
    Displays the receipt data from the session and then clears it.
    """
    receipt_data = session.pop('receipt_data', None)
    if not receipt_data:
        flash("ไม่พบข้อมูลใบเสร็จ. โปรดดำเนินการคำสั่งซื้อใหม่.", 'danger')
        return redirect(url_for('cart'))
    return render_template("receipt_png_template.html",
                           orders=receipt_data['orders'],
                           barcode_id=receipt_data['barcode_id'],
                           total_quantity=receipt_data['total_quantity'],
                           total_price=receipt_data['total_price'],
                           current_order_id=receipt_data['current_order_id'])

# --- Routes สำหรับแก้ไขและลบรายการในตะกร้า (ย้ายมาอยู่นอกฟังก์ชัน cart()) ---
# แก้ไขรายการในตะกร้า
@app.route("/cart/edit/<int:item_id>", methods=["POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Viewer can edit their temp store data
def edit_cart_item(item_id):
    conn_edit = get_db_connection()
    cursor_edit = None # Initialize cursor to None
    if not conn_edit:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('cart'))
    try:
        cursor_edit = conn_edit.cursor(dictionary=True)
        new_quantity = int(request.form['quantity'])
        new_disquantity = int(request.form['disquantity'])
        original_product_id = request.form['products_id'] # ต้องส่ง products_id มาด้วย
        original_order_id = request.form['order_id'] # ต้องส่ง order_id มาด้วย
        item_store_id = int(request.form['item_store_id']) # Store ID of the item being edited

        # Permission check for editing
        auth_ok = False
        if session.get('role') in ['root_admin', 'administrator']:
            auth_ok = True
        elif session.get('role') in ['moderator', 'viewer'] and item_store_id == session.get('store_id'):
            auth_ok = True
        elif session.get('role') == 'member' and item_store_id == session.get('store_id') and request.form['email'] == session.get('email'):
            auth_ok = True
        
        if not auth_ok:
            flash("คุณไม่มีสิทธิ์แก้ไขรายการนี้", 'danger')
            return redirect(url_for('cart'))

        # ดึงข้อมูลสินค้านั้นๆ เพื่อตรวจสอบสต็อกและ store_id
        cursor_edit.execute("SELECT stock, products_name, store_id FROM tbl_products WHERE products_id = %s", (original_product_id,))
        product_info_raw = cursor_edit.fetchone()
        if not product_info_raw:
            flash(f"ไม่พบสินค้า ID {original_product_id} สำหรับแก้ไข.", 'danger')
            return redirect(url_for('cart'))
        
        product_info = product_info_raw.copy()
        # Ensure product_info['stock'] is treated as a number
        current_stock = int(product_info['stock'] or 0)

        # Ensure product also belongs to the user's store context
        if product_info['store_id'] != session['store_id']: # Use session['store_id'] for current user's context
             flash("สินค้าไม่ได้อยู่ในร้านค้าของคุณ/ที่รับผิดชอบ!", 'danger')
             return redirect(url_for('cart'))

        # ดึงปริมาณเดิมของรายการในคำสั่งซื้อเพื่อคำนวณการเปลี่ยนแปลงสต็อก
        cursor_edit.execute("SELECT quantity FROM tbl_order WHERE id = %s", (item_id,))
        current_order_qty_result = cursor_edit.fetchone()
        current_order_qty = current_order_qty_result['quantity'] if current_order_qty_result else 0

        # คำนวณความแตกต่างของจำนวนที่เปลี่ยนไป
        qty_change = new_quantity - current_order_qty
        if new_quantity <= 0:
            flash("จำนวนสินค้าต้องมากกว่า 0 หากต้องการลบ กรุณากดปุ่มลบ.", 'warning')
            return redirect(url_for('cart'))
        
        if current_stock < qty_change:
            flash(f"ไม่สามารถแก้ไขได้: สินค้า {product_info['products_name']} มีสต็อกไม่พอ. มีในสต็อก: {current_stock} ต้องการเพิ่ม {qty_change} ชิ้น", 'danger')
            return redirect(url_for('cart'))

        cursor_edit.execute("""
            UPDATE tbl_order
            SET quantity = %s, disquantity = %s
            WHERE id = %s AND order_id = %s AND store_id = %s
        """, (new_quantity, new_disquantity, item_id, original_order_id, item_store_id)) # Added store_id to WHERE
        
        # อัปเดตสต็อกใน tbl_products
        cursor_edit.execute("UPDATE tbl_products SET stock = stock - %s WHERE products_id = %s", (qty_change, original_product_id))
        conn_edit.commit()
        flash(f'แก้ไขรายการ ID {item_id} ในคำสั่งซื้อ {original_order_id} สำเร็จแล้ว!', 'success')
    except ValueError:
        flash("จำนวนและทิ้งต้องเป็นตัวเลขที่ถูกต้อง.", 'danger')
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการแก้ไขรายการ: {err}", 'danger')
        conn_edit.rollback()
    finally:
        if cursor_edit:
            cursor_edit.close()
        if conn_edit:
            conn_edit.close()
    return redirect(url_for('cart'))

# ลบรายการในตะกร้า
@app.route("/cart/delete/<int:item_id>", methods=["POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Viewer can delete their temp store data
def delete_cart_item(item_id):
    conn_del = get_db_connection()
    cursor_del = None # Initialize cursor to None
    if not conn_del:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('cart'))
    try:
        cursor_del = conn_del.cursor(dictionary=True)
        # ดึงข้อมูลรายการที่จะลบ เพื่อคืนสต็อกและตรวจสอบ store_id
        cursor_del.execute("SELECT products_id, quantity, order_id, store_id, email FROM tbl_order WHERE id = %s", (item_id,))
        item_to_delete = cursor_del.fetchone()
        if not item_to_delete:
            flash("ไม่พบรายการที่จะลบ.", 'danger')
            return redirect(url_for('cart'))
        
        item_store_id = item_to_delete['store_id']

        # Permission check for deleting
        auth_ok = False
        if session.get('role') in ['root_admin', 'administrator']:
            auth_ok = True
        elif session.get('role') in ['moderator', 'viewer'] and item_store_id == session.get('store_id'):
            auth_ok = True
        elif session.get('role') == 'member' and item_store_id == session.get('store_id') and item_to_delete['email'] == session.get('email'):
            auth_ok = True
        
        if not auth_ok:
            flash("คุณไม่มีสิทธิ์ลบรายการนี้", 'danger')
            return redirect(url_for('cart'))


        # คืนสต็อกสินค้า
        cursor_del.execute("UPDATE tbl_products SET stock = stock + %s WHERE products_id = %s",
                           (item_to_delete['quantity'], item_to_delete['products_id']))
        # ลบรายการออกจาก tbl_order
        cursor_del.execute("DELETE FROM tbl_order WHERE id = %s AND store_id = %s", (item_id, item_store_id)) # Added store_id to WHERE
        
        conn_del.commit()
        flash(f'ลบรายการ ID {item_id} ออกจากคำสั่งซื้อ {item_to_delete["order_id"]} สำเร็จแล้ว! สต็อกสินค้าได้รับการคืนแล้ว.', 'success')
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการลบรายการ: {err}", 'danger')
        conn_del.rollback()
    finally:
        if cursor_del:
            cursor_del.close()
        if conn_del:
            conn_del.close()
    return redirect(url_for('cart'))

# --- Route to manage package returns (bin) ---
@app.route("/bin", methods=["GET", "POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def bin():
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    orders_data = [] # Data for order items to display
    barcode_id_filter = request.args.get('barcode_id_filter', '') # For GET requests (search/reset)
    request_form_data = {} # Stores POST form data to persist values in the form

    current_user_store_id = session.get('store_id')
    if not current_user_store_id and session.get('role') in ['moderator', 'member', 'viewer']:
        flash("คุณยังไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
        return redirect(url_for('index'))

    # If it's a POST request, store form data and update barcode_id_filter if necessary
    if request.method == 'POST':
        request_form_data = request.form.to_dict() # Store all POST form data
        if request.form.get('action') == 'add_disquantity':
            # If adding disquantity, use the barcode_id from the form
            barcode_id_filter = request.form.get('barcode_id_for_disquantity', barcode_id_filter)
        elif request.form.get('action') == 'search':
            # If performing a search, use the barcode_id from the search input
            barcode_id_filter = request.form.get('barcode_id_filter_input', barcode_id_filter)

    # --- Logic for incrementing Disquantity (+1) ---
    if request.method == "POST" and request.form.get('action') == 'add_disquantity':
        # Viewers can modify data in their temp store
        barcode_id_to_search = request.form.get('barcode_id_for_disquantity')
        products_id_to_disquantity = request.form.get('products_id_to_disquantity')
        
        if not barcode_id_to_search or not products_id_to_disquantity:
            flash("กรุณาระบุรหัสบาร์โค้ดและรหัสสินค้าที่ต้องการเพิ่มจำนวนทิ้ง.", 'danger')
            return render_template("bin.html", orders=[], barcode_id_filter=barcode_id_filter, request_form_data=request_form_data)

        try:
            cursor = conn.cursor(dictionary=True) # Open cursor here for this action
            # Search for the item in tbl_order matching barcode_id and products_id AND store_id
            cursor.execute("""
                SELECT o.id, o.quantity, o.disquantity, o.products_name, o.products_id, p.stock, p.category_id, o.store_id
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                WHERE o.barcode_id = %s AND o.products_id = %s AND o.store_id = %s
            """, (barcode_id_to_search, products_id_to_disquantity, current_user_store_id)) # Filter by store_id
            order_item_to_update_raw = cursor.fetchone()

            if order_item_to_update_raw:
                order_item_to_update = order_item_to_update_raw.copy()
                # Ensure stock is converted to int
                order_item_to_update['stock'] = int(order_item_to_update['stock'] or 0)

                current_quantity = order_item_to_update['quantity']
                current_disquantity = order_item_to_update['disquantity']
                product_id = order_item_to_update['products_id']
                category_id_for_bin = order_item_to_update.get('category_id')

                if category_id_for_bin is None:
                    flash(f"ไม่พบ category_id สำหรับสินค้า '{order_item_to_update['products_name']}'. ไม่สามารถอัปเดต bin ได้.", 'danger')
                    return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))

                proposed_disquantity = current_disquantity + 1
                
                # Check if the proposed disquantity does not exceed the total quantity
                if proposed_disquantity <= current_quantity:
                    # Update disquantity in tbl_order
                    cursor.execute("UPDATE tbl_order SET disquantity = %s WHERE id = %s",
                                   (proposed_disquantity, order_item_to_update['id']))
                    
                    # UPDATED: Set value in tbl_bin to 1 where category_id matches the product's category_id AND store_id
                    cursor.execute("UPDATE tbl_bin SET value = 1 WHERE category_id = %s", # Assuming tbl_bin is not store-specific for simplicity
                                   (category_id_for_bin,))
                    conn.commit()
                    flash(f"เพิ่มจำนวนทิ้งสินค้า '{order_item_to_update['products_name']}' (รหัสสินค้า: {products_id_to_disquantity}) สำเร็จ. สถานะ bin (category_id: {category_id_for_bin}) ได้รับการอัปเดตแล้ว.", 'success')
                else:
                    flash(f"ไม่สามารถเพิ่มจำนวนทิ้งได้เกินจำนวนสินค้าที่มีอยู่ ({current_quantity} ชิ้น) สำหรับสินค้า '{order_item_to_update['products_name']}'", 'danger')
            else:
                flash("ไม่พบรายการสินค้าที่ตรงกันสำหรับรหัสบาร์โค้ดและรหัสสินค้าที่ระบุในร้านค้าของคุณ.", 'danger')
        except mysql.connector.Error as err:
            flash(f"เกิดข้อผิดพลาดในการดำเนินการ: {err}", 'danger')
            conn.rollback() # Rollback in case of error
        finally:
            # Always close the database connection
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            # Redirect back to the bin page with the current barcode_id_filter to show filtered results
            return redirect(url_for('bin', barcode_id_filter=barcode_id_filter))

    # --- Main logic สำหรับการแสดงผลตารางคำสั่งซื้อ (GET requests หรือ POST ที่ไม่ใช่ action 'add_disquantity') ---
    try:
        cursor = conn.cursor(dictionary=True) # Ensure cursor is opened for this block
        if barcode_id_filter:
            # ดึงเฉพาะรายการที่มี barcode_id ตรงกับ filter และ store_id ของผู้ใช้
            base_query = """
                SELECT o.*, p.price, p.products_name, p.category_id, s.store_name
                FROM tbl_order o
                JOIN tbl_products p ON o.products_id = p.products_id
                LEFT JOIN tbl_stores s ON o.store_id = s.store_id
                WHERE o.barcode_id = %s AND o.store_id = %s
            """
            query_params = (barcode_id_filter, current_user_store_id)

            # เพิ่มตัวกรองอีเมลสำหรับบทบาท 'member'
            if session.get('role') == 'member':
                base_query += " AND o.email = %s"
                query_params += (session['email'],)
            
            base_query += " ORDER BY o.id DESC"
            cursor.execute(base_query, query_params)
            orders_data_raw = cursor.fetchall()

            orders_data = []
            for o_raw in orders_data_raw:
                o = o_raw.copy()
                o['price'] = float(o['price'] or 0.0) # Convert price to float, default 0.0
                orders_data.append(o)

        else:
            # หากไม่มี barcode_id_filter จะไม่แสดงข้อมูลใดๆ
            orders_data = [] # แสดงตารางว่างเปล่าหากไม่มีการค้นหา
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการดึงข้อมูลคำสั่งซื้อ: {err}", 'danger')
        orders_data = []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    current_auto_order_id = "N/A" # หรือจะเอามาจาก session เก่าก็ได้หากต้องการ แต่ตอนนี้เน้น barcode_id_filter
    return render_template("bin.html",
                           orders=orders_data,
                           barcode_id_filter=barcode_id_filter,
                           request_form_data=request_form_data, # ส่งข้อมูลฟอร์มที่กรอกไป
                           current_auto_order_id=current_auto_order_id # อาจจะเอาออกไปเลยก็ได้
                           )

# --- Routes สำหรับแก้ไขและลบรายการในระบบคืนบรรจุภัณฑ์ ---
# แก้ไขรายการในระบบคืนบรรจุภัณฑ์
@app.route("/bin/edit/<int:item_id>", methods=["POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Viewer can edit their temp store data
def edit_bin_item(item_id):
    conn_edit = get_db_connection()
    cursor_edit = None # Initialize cursor to None
    if not conn_edit:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('bin'))
    try:
        cursor_edit = conn_edit.cursor(dictionary=True)
        new_quantity = int(request.form['quantity'])
        new_disquantity = int(request.form['disquantity'])
        original_product_id = request.form['products_id']
        original_order_id = request.form['order_id']
        item_barcode_id = request.form.get('barcode_id', '') # Get barcode_id for redirect

        # Get current user's store_id
        current_user_store_id = session.get('store_id')
        if not current_user_store_id:
            flash("คุณยังไม่มีร้านค้าที่ผูกไว้. โปรดติดต่อผู้ดูแลระบบ.", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

        # ดึงข้อมูลเก่าของรายการใน tbl_order และข้อมูลสินค้า (รวม store_id)
        cursor_edit.execute("SELECT quantity, disquantity, store_id, email FROM tbl_order WHERE id = %s", (item_id,))
        old_order_item = cursor_edit.fetchone()
        if not old_order_item:
            flash(f"ไม่พบรายการ ID {item_id} สำหรับแก้ไข.", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))
        
        item_store_id = old_order_item['store_id']

        # Permission check for editing
        auth_ok = False
        if session.get('role') in ['root_admin', 'administrator']:
            auth_ok = True
        elif session.get('role') in ['moderator', 'viewer'] and item_store_id == current_user_store_id:
            auth_ok = True
        elif session.get('role') == 'member' and item_store_id == current_user_store_id and old_order_item['email'] == session.get('email'):
            auth_ok = True
        
        if not auth_ok:
            flash("คุณไม่มีสิทธิ์แก้ไขรายการนี้", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

        old_quantity = old_order_item['quantity']
        old_disquantity = old_order_item['disquantity']

        # ดึงสต็อกปัจจุบันของสินค้าจาก tbl_products (รวม store_id)
        cursor_edit.execute("SELECT stock, products_name, store_id FROM tbl_products WHERE products_id = %s", (original_product_id,))
        product_info_raw = cursor_edit.fetchone()
        if not product_info_raw:
            flash(f"ไม่พบข้อมูลสินค้า ID {original_product_id}.", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))
        
        product_info = product_info_raw.copy()
        # Ensure product_info['stock'] is treated as a number
        current_stock = int(product_info['stock'] or 0)

        # Ensure product also belongs to the user's store context
        if product_info['store_id'] != current_user_store_id:
            flash("สินค้าไม่ได้อยู่ในร้านค้าของคุณ/ที่รับผิดชอบ!", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

        # --- VALIDATION ---
        if new_quantity <= 0:
            flash("จำนวนสินค้าต้องมากกว่า 0 หากต้องการลบ กรุณากดปุ่มลบรายการ.", 'warning')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))
        
        if new_disquantity < 0:
            flash("จำนวนทิ้งต้องไม่น้อยกว่า 0.", 'warning')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))
        
        if new_disquantity > new_quantity:
            flash("จำนวนทิ้งไม่สามารถมากกว่าจำนวนสินค้าทั้งหมดได้.", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

        # --- คำนวณการเปลี่ยนแปลงสต็อก ---
        stock_change_from_quantity = old_quantity - new_quantity
        stock_change_from_disquantity = new_disquantity - old_disquantity
        total_stock_adjustment = stock_change_from_quantity + stock_change_from_disquantity

        if current_stock + total_stock_adjustment < 0:
            flash(f"ไม่สามารถแก้ไขได้: สินค้า '{product_info['products_name']}' มีสต็อกไม่พอสำหรับการเปลี่ยนแปลงนี้ (สต็อกปัจจุบัน: {current_stock}, ต้องการปรับ: {total_stock_adjustment}).", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

        # อัปเดต tbl_order
        cursor_edit.execute("""
            UPDATE tbl_order
            SET quantity = %s, disquantity = %s
            WHERE id = %s AND order_id = %s AND store_id = %s
        """, (new_quantity, new_disquantity, item_id, original_order_id, current_user_store_id)) # Added store_id to WHERE
        
        # อัปเดตสต็อกใน tbl_products
        cursor_edit.execute("UPDATE tbl_products SET stock = stock + %s WHERE products_id = %s",
                            (total_stock_adjustment, original_product_id))
        conn_edit.commit()
        flash(f'แก้ไขรายการ ID {item_id} (สินค้า: {product_info["products_name"]}) ในคำสั่งซื้อ {original_order_id} สำเร็จแล้ว!', 'success')
    except ValueError:
        flash("จำนวนและทิ้งต้องเป็นตัวเลขที่ถูกต้อง.", 'danger')
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการแก้ไขรายการ: {err}", 'danger')
        conn_edit.rollback()
    finally:
        if cursor_edit:
            cursor_edit.close()
        if conn_edit:
            conn_edit.close()
    
    return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

# ลบรายการในระบบคืนบรรจุภัณฑ์
@app.route("/bin/delete/<int:item_id>", methods=["POST"])
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer']) # Viewer can delete their temp store data
def delete_bin_item(item_id):
    conn_del = get_db_connection()
    cursor_del = None # Initialize cursor to None
    item_barcode_id = "" # ตัวแปรสำหรับเก็บ barcode_id เพื่อ redirect กลับไปหน้าเดิม
    if not conn_del:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('bin'))
    try:
        cursor_del = conn_del.cursor(dictionary=True)
        # ดึงข้อมูลรายการที่จะลบ เพื่อคืนสต็อกและเก็บ barcode_id (รวม store_id)
        cursor_del.execute("SELECT products_id, quantity, order_id, barcode_id, store_id, email FROM tbl_order WHERE id = %s", (item_id,))
        item_to_delete = cursor_del.fetchone()
        if not item_to_delete:
            flash("ไม่พบรายการที่จะลบ.", 'danger')
            return redirect(url_for('bin'))
        
        item_barcode_id = item_to_delete['barcode_id'] # เก็บ barcode_id ไว้
        item_store_id = item_to_delete['store_id']

        # Permission check for deleting
        auth_ok = False
        if session.get('role') in ['root_admin', 'administrator']:
            auth_ok = True
        elif session.get('role') in ['moderator', 'viewer'] and item_store_id == session.get('store_id'):
            auth_ok = True
        elif session.get('role') == 'member' and item_store_id == session.get('store_id') and item_to_delete['email'] == session.get('email'):
            auth_ok = True
        
        if not auth_ok:
            flash("คุณไม่มีสิทธิ์ลบรายการนี้", 'danger')
            return redirect(url_for('bin', barcode_id_filter=item_barcode_id))


        # คืนสต็อกสินค้า (คืนตามจำนวน quantity ทั้งหมดของรายการนั้น)
        cursor_del.execute("UPDATE tbl_products SET stock = stock + %s WHERE products_id = %s",
                            (item_to_delete['quantity'], item_to_delete['products_id']))
        # ลบรายการออกจาก tbl_order
        cursor_del.execute("DELETE FROM tbl_order WHERE id = %s AND store_id = %s", (item_id, item_store_id)) # Added store_id to WHERE
        
        conn_del.commit()
        flash(f'ลบรายการ ID {item_id} ออกจากคำสั่งซื้อ {item_to_delete["order_id"]} สำเร็จแล้ว! สต็อกสินค้าได้รับการคืนแล้ว.', 'success')
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการลบรายการ: {err}", 'danger')
    finally:
        if cursor_del:
            cursor_del.close()
        if conn_del:
            conn_del.close()
    
    return redirect(url_for('bin', barcode_id_filter=item_barcode_id))

@app.route("/export_orders_pdf")
@role_required(['root_admin', 'administrator', 'moderator', 'member', 'viewer'])
def export_orders_pdf():
    """Exports order data to a PDF file."""
    conn = get_db_connection()
    cursor = None # Initialize cursor to None
    if not conn:
        flash("เกิดข้อผิดพลาดในการเชื่อมต่อฐานข้อมูล.", 'danger')
        return redirect(url_for('tbl_order'))
    try:
        cursor = conn.cursor(dictionary=True)
        base_query = """
            SELECT 
                o.id, 
                o.order_id, 
                o.products_id, 
                o.products_name, 
                o.quantity, 
                o.disquantity, 
                o.email, 
                o.order_date,
                o.barcode_id, 
                p.category_id,
                p.price,
                s.store_name -- Include store_name
            FROM tbl_order o
            LEFT JOIN tbl_products p ON o.products_id = p.products_id
            LEFT JOIN tbl_stores s ON o.store_id = s.store_id
        """
        query_params = []
        if session.get('role') == 'member':
            base_query += " WHERE o.email = %s AND o.store_id = %s"
            query_params.extend([session['email'], session['store_id']])
        elif session.get('role') in ['moderator', 'viewer']: # Filter for moderator/viewer
            base_query += " WHERE o.store_id = %s"
            query_params.append(session['store_id'])
        
        base_query += " ORDER BY o.order_date DESC"
        
        cursor.execute(base_query, tuple(query_params))
        orders_raw = cursor.fetchall()

        orders = []
        for order_raw in orders_raw:
            order = order_raw.copy()
            order['price'] = float(order['price'] or 0.0) # Ensure price is float
            orders.append(order)

        # Render HTML template for PDF
        html = render_template("pdf_template_orders.html", orders=orders)
        
        # Create a PDF from HTML
        pdf_buffer = BytesIO()
        pisa_status = pisa.CreatePDF(
            html,                # the HTML to convert
            dest=pdf_buffer)     # file handle to receive result
        if pisa_status.err:
            flash(f"เกิดข้อผิดพลาดในการสร้าง PDF: {pisa_status.err}", 'danger')
            return redirect(url_for('tbl_order'))
        pdf_buffer.seek(0)
        
        response = make_response(pdf_buffer.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=orders_report.pdf"
        response.headers["Content-type"] = "application/pdf"
        return response
    except mysql.connector.Error as err:
        flash(f"เกิดข้อผิดพลาดในการส่งออกรายงานคำสั่งซื้อ: {err}", 'danger')
        return redirect(url_for('tbl_order'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
if __name__ == '__main__':
    app.run(port=5000)