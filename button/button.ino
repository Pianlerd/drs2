#include <WiFi.h>
#include <HTTPClient.h>

const char* ssid = "EMB5325";
const char* password = "cdti12345";

// ใส่ IP คอมพิวเตอร์ที่รัน Flask เช่น 192.168.1.100
String serverName = "http://192.168.1.141:5000/update_button";

#define BUTTON_PIN 33

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi...");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("Connected!");
}

void loop() {
  int buttonState = digitalRead(BUTTON_PIN);

  if (buttonState == LOW) {  // ปุ่มกด
    sendToServer("pressed");
    delay(500); // กันการส่งซ้ำเร็วเกินไป
  }
  else {
    sendToServer("released");
    delay(500);
  }
}

void sendToServer(String state) {
  if(WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(serverName);
    http.addHeader("Content-Type", "application/x-www-form-urlencoded");

    String postData = "state=" + state;
    int httpResponseCode = http.POST(postData);

    if(httpResponseCode > 0) {
      Serial.println("Server response: " + http.getString());
    } else {
      Serial.println("Error sending: " + String(httpResponseCode));
    }
    http.end();
  }
}
