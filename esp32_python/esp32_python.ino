#include <WiFi.h>
#include <WebServer.h>

// กำหนดข้อมูล WiFi
const char* ssid = "Galaxy Note10+306b";
const char* password = "";

WebServer server(80);

// กำหนด GPIO ของ LED
int ledPins[5] = {26, 27,14, 12, 13};

void handleLED(int index, bool on) {
  digitalWrite(ledPins[index], on ? HIGH : LOW);
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println(WiFi.localIP());

  for (int i = 0; i < 5; i++) {
    pinMode(ledPins[i], OUTPUT);
    digitalWrite(ledPins[i], LOW);
  }

  for (int i = 0; i < 5; i++) {
    String onPath = "/led" + String(i + 1) + "/on";
    String offPath = "/led" + String(i + 1) + "/off";

    server.on(onPath.c_str(), [i]() {
      handleLED(i, true);
      server.send(200, "text/plain", "LED on");
    });

    server.on(offPath.c_str(), [i]() {
      handleLED(i, false);
      server.send(200, "text/plain", "LED off");
    });
  }

  server.begin();
}

void loop() {
  server.handleClient();
}
