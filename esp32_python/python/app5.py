from flask import Flask, render_template, request, redirect
import mysql.connector
import requests
import threading
import time


app = Flask(__name__)
ESP32_IP = "http://192.168.208.124"

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
    return mysql.connector.connect(**DB_CONFIG)

def check_and_control_led_loop():
    global led_cache
    global last_error_message, error_occurrence_count, normal_status_printed

    while True:
        try:
            # --- ส่วนการทำงานปกติ (ไม่มี Error) ---
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, value FROM tbl_bin")
            rows = cursor.fetchall()
            conn.close()

            # ถ้าตอนนี้ไม่มี Error และเคยมี Error มาก่อน ให้แจ้งว่ากลับมาปกติ
            if last_error_message is not None:
                print(f"[Recovered] ระบบกลับมาทำงานปกติหลังจากเกิดข้อผิดพลาด: {last_error_message} ({error_occurrence_count} ครั้ง)")
                last_error_message = None
                error_occurrence_count = 0
                normal_status_printed = True # ตั้งค่าว่าได้พิมพ์สถานะปกติไปแล้ว

            # หากสถานะปกติและยังไม่เคยพิมพ์ ให้พิมพ์แจ้ง
            elif not normal_status_printed:
                print("[Status] ระบบทำงานปกติ")
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
                        # แยก Error การเชื่อมต่อ ESP32
                        current_error = f"ไม่สามารถสั่ง ESP32 สำหรับ LED {led_id} - {req_e}"
                        if current_error != last_error_message:
                            # เป็น Error ใหม่
                            print(f"[Error] {current_error}")
                            last_error_message = current_error
                            error_occurrence_count = 1
                            normal_status_printed = False # เกิด Error แล้ว ไม่ใช่สถานะปกติ
                        else:
                            # เป็น Error เดิม แต่เพิ่มจำนวนครั้ง
                            error_occurrence_count += 1
                            # ไม่ต้องพิมพ์ซ้ำเพื่อไม่ให้ขึ้นรัวๆ
                            # print(f"[Error Count] LED {led_id} Error: {error_occurrence_count} times (same as before)") # ถ้าอยากเห็นจำนวนครั้งที่ Error เดิมเกิดขึ้น
            
        except mysql.connector.Error as db_err:
            # แยก Error การเชื่อมต่อฐานข้อมูล
            current_error = f"ข้อผิดพลาดฐานข้อมูล: {db_err}"
            if current_error != last_error_message:
                # เป็น Error ใหม่
                print(f"[Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                # เป็น Error เดิม แต่เพิ่มจำนวนครั้ง
                error_occurrence_count += 1
                # ไม่ต้องพิมพ์ซ้ำ
                # print(f"[Error Count] Database Error: {error_occurrence_count} times (same as before)")

        except Exception as e:
            # Catch All Other Errors
            current_error = f"ข้อผิดพลาดทั่วไปในลูป: {e}"
            if current_error != last_error_message:
                print(f"[Loop Error] {current_error}")
                last_error_message = current_error
                error_occurrence_count = 1
                normal_status_printed = False
            else:
                error_occurrence_count += 1
                # ไม่ต้องพิมพ์ซ้ำ

        finally:
            time.sleep(0.1) # ตรวจสอบทุก 100 มิลลิวินาที

# ... (ส่วนโค้ด Flask ที่เหลือเหมือนเดิม) ...

@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, value FROM tbl_bin")
    rows = cursor.fetchall()
    conn.close()
    led_data = {row['id']: row['value'] for row in rows}
    return render_template('led_buttons.html', led_data=led_data)

@app.route('/toggle/<int:led_id>', methods=['POST'])
def toggle_led(led_id):
    if led_id in LED_PINS:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT value FROM tbl_bin WHERE id = %s", (led_id,))
            result = cursor.fetchone()
            if result is not None:
                new_value = 0 if result['value'] == 1 else 1
                cursor.execute("UPDATE tbl_bin SET value = %s WHERE id = %s", (new_value, led_id))
                conn.commit()
            conn.close()
        except Exception as e:
            # คุณสามารถเพิ่มการจัดการ Error ที่นี่ได้เช่นกัน หากต้องการแสดงผลที่หน้าเว็บ
            return f"Error: {e}", 500
    return redirect('/')






if __name__ == '__main__':
    threading.Thread(target=check_and_control_led_loop, daemon=True).start()
    app.run(debug=True, host='0.0.0.0')