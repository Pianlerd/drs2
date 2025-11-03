#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ========================================
// ตั้งค่า WiFi - เปลี่ยนเป็นของคุณ
// ========================================
const char* ssid = "Galaxy Note10+306b";
const char* password = "";

// ========================================
// ตั้งค่า Bolt Database - เปลี่ยนเป็นของคุณ
// ========================================
const char* Bolt DatabaseUrl = "https://1";
const char* Bolt DatabaseKey = "your-Bolt Database-anon-key-here";

// ========================================
// ตั้งค่าขาปุ่ม
// ========================================
const int BUTTON_PIN = 33;

// ========================================
// ตัวแปรสำหรับ debounce
// ========================================
unsigned long lastDebounceTime = 0;
unsigned long debounceDelay = 200;
int lastButtonState = HIGH;

void setup() {
  Serial.begin(115200);
  
  // ตั้งค่าขาปุ่มเป็น INPUT_PULLUP
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  
  // เชื่อมต่อ WiFi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\n✅ Connected to WiFi");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
}

void loop() {
  int buttonState = digitalRead(BUTTON_PIN);
  
  // ตรวจสอบว่าปุ่มถูกกด (LOW เพราะใช้ INPUT_PULLUP)
  if (buttonState == LOW && lastButtonState == HIGH) {
    unsigned long currentTime = millis();
    
    // Debounce - ป้องกันการกดซ้ำเร็วเกินไป
    if ((currentTime - lastDebounceTime) > debounceDelay) {
      lastDebounceTime = currentTime;
      
      Serial.println("🔴 Button 33 pressed! Sending to Supabase...");
      updateButtonStatus(true);
    }
  }
  
  lastButtonState = buttonState;
  delay(50);
}

void updateButtonStatus(bool pressed) {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    
    // สร้าง URL สำหรับ Bolt Database REST API
    String url = String(Bolt DatabaseUrl) + "/rest/v1/button_status?id=eq.1";
    
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("apikey", Bolt DatabaseKey);
    http.addHeader("Authorization", String("Bearer ") + Bolt DatabaseKey);
    http.addHeader("Prefer", "return=minimal");
    
    // สร้าง JSON payload
    StaticJsonDocument<200> doc;
    doc["button_33"] = pressed;
    
    String jsonString;
    serializeJson(doc, jsonString);
    
    Serial.print("Sending: ");
    Serial.println(jsonString);
    
    // ส่ง PATCH request
    int httpResponseCode = http.PATCH(jsonString);
    
    if (httpResponseCode > 0) {
      Serial.print("✅ HTTP Response code: ");
      Serial.println(httpResponseCode);
      
      if (httpResponseCode == 204 || httpResponseCode == 200) {
        Serial.println("✅ Button status updated successfully!");
      }
    } else {
      Serial.print("❌ Error code: ");
      Serial.println(httpResponseCode);
    }
    
    http.end();
  } else {
    Serial.println("❌ WiFi Disconnected");
  }
}
