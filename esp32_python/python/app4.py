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
led_cache = {}  # เก็บค่าเดิมไว้เพื่อตรวจว่าเปลี่ยนจริงไหม

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def check_and_control_led_loop():
    global led_cache
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT id, value FROM tbl_bin")
            rows = cursor.fetchall()
            conn.close()

            for row in rows:
                led_id = row["id"]
                value = row["value"]
                action = "on" if value == 1 else "off"

                # ตรวจสอบว่ามีการเปลี่ยนค่าไหม
                if led_id not in led_cache or led_cache[led_id] != value:
                    led_cache[led_id] = value
                    try:
                        requests.get(f"{ESP32_IP}/led{led_id}/{action}", timeout=2)
                        print(f"[Update] LED {led_id} → {action.upper()}")
                    except:
                        print(f"[Error] ไม่สามารถสั่ง ESP32 สำหรับ LED {led_id}")
        except Exception as e:
            print(f"[Loop Error] {e}")

        time.sleep(3)  # รอ 3 วินาที

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
            return f"Error: {e}", 500
    return redirect('/')

if __name__ == '__main__':
    # เริ่ม background thread ทันทีเมื่อ Flask รัน
    threading.Thread(target=check_and_control_led_loop, daemon=True).start()
    app.run(debug=True, host='0.0.0.0')
