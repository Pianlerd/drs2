#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <HTTPClient.h>
#include <TFT_eSPI.h>
#include <SPI.h>

// Network credentials
const char* ssid = "Galaxy";    
const char* password = "nanos1234";

// Original Servo Configuration
const int servoPins[] = { 12, 13, 14, 26, 27 };
const int NUM_SERVOS = sizeof(servoPins) / sizeof(servoPins[0]);
Servo myServos[NUM_SERVOS];
int currentServoAngles[NUM_SERVOS];
int targetServoAngles[NUM_SERVOS];
bool isServoMoving[NUM_SERVOS];
unsigned long servoMoveStartTime[NUM_SERVOS];
const unsigned long SERVO_MOVE_DURATION = 5000;

// ====== New Servo and Button Configuration ======
const int newServoPin = 15;   // Assigning to an unused pin
const int newButtonPin = 33;  // Assigning to an unused pin
const int angleStart = 0;     // Start angle
const int angleEnd = 180;     // End angle
const int delayTime = 500;    // Delay in milliseconds (used as interval for non-blocking)
int totalRounds = 0;          // Number of rounds for the servo to turn, starts at 0
// bool isNewServoRunning = false; // ถูกยกเลิกการใช้

Servo newServo;

// ====== New Servo Control Variables (Non-Blocking) ======
enum ServoState { STATE_START,
                  STATE_DELAY1,
                  STATE_END,
                  STATE_DELAY2,
                  STATE_IDLE };
ServoState newServoState = STATE_IDLE;  // สถานะเริ่มต้นคือไม่ทำงาน
unsigned long newServoStateStartTime = 0;
// ========================================================

WebServer server(80);

// IR Sensor Configuration
#define IR_SENSOR_PIN 32
int count = 0;
bool objectDetected = false;

// Flask Server IP
const char* flask_ip = "192.168.84.140";  // ต้องตรงกับ IP ปัจจุบันของคอมพิวเตอร์

// TFT Display Settings
TFT_eSPI tft = TFT_eSPI();

// Cyberpunk Color Theme
#define COLOR_BACKGROUND 0x0000
#define COLOR_MATRIX_GREEN 0x07E0
#define COLOR_CYBER_RED 0xF800
#define COLOR_DARK_PANEL 0x1082
#define COLOR_BORDER 0x0410

// Display Dimensions
const int SCREEN_WIDTH = 480;
const int SCREEN_HEIGHT = 320;

// --- UI State and Time Management ---
enum UIState {
  STATE_BOOT,
  STATE_CONNECTING_WIFI,
  STATE_CONNECTION_SUCCESS,
  STATE_DASHBOARD,
  STATE_SHOW_MESSAGE
};
UIState currentUIState = STATE_BOOT;
unsigned long lastStateChangeTime = 0;
String messageToShow = "";
bool needsRedraw = true;

// Prototypes for new non-blocking function
void updateNewServo();
void showCyberDashboard();
void showNetworkHacking();
void showNetworkCompromised();
void showSecurityBreach(String message);
void drawCyberLoadingBar();
void updateHackingProgress(int attempt);
void displayReceivedMessage(String message);
void handleRoot();
void handleServo(int servoIndex, int state);
void updateServoPositions();
void handleNotFound();
void sendResetCommandToFlask();
void updateCounterDisplay();
void updateServoStatusDisplay();
void drawCyberHeader(String title);
void drawCyberPanel(int x, int y, int w, int h, String title);
void setState(UIState newState);
void showCyberpunkBoot();
void setupWebServer();
void initializeTFTDisplay();
void initializeServoMotors();
void handleUIState();



void setup() {
  Serial.begin(115200);


  initializeTFTDisplay();
  initializeServoMotors();
  pinMode(IR_SENSOR_PIN, INPUT_PULLUP);

  // ====== Initialize New Servo and Button ======
  pinMode(newButtonPin, INPUT_PULLUP);  // Set button pin with internal pull-up resistor
  newServo.attach(newServoPin);
  newServo.write(angleStart);  // Move to the starting position
  Serial.println("New servo and button initialized.");
  // ===========================================

  // Start WiFi connection process (non-blocking)
  WiFi.begin(ssid, password);
  Serial.print("Attempting to connect to WiFi: ");
  Serial.println(ssid);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected!");
  Serial.print("ESP32 IP: ");
  Serial.println(WiFi.localIP());

  setupWebServer();
}

void loop() {
  server.handleClient();
  updateServoPositions();
  handleIRSensor();
  handleUIState();
  handleNewServoAndButton();
  updateNewServo();  // <<< New Non-Blocking Servo Handler
}

// ---------------------------------------------
// --- NEW/MODIFIED FUNCTION: Non-Blocking Servo Control ---
// ---------------------------------------------

void updateNewServo() {
  if (newServoState == STATE_IDLE) {
    return;  // ไม่ต้องทำอะไรถ้านิ่งอยู่
  }

  unsigned long currentTime = millis();

  switch (newServoState) {
    case STATE_DELAY1:  // รอเมื่อหมุนไป angleEnd
      if (currentTime - newServoStateStartTime >= delayTime) {
        newServo.write(angleStart);  // หมุนกลับไป Start
        newServoStateStartTime = currentTime;
        newServoState = STATE_DELAY2;  // เข้าสู่สถานะรอหน่วงเวลาที่สอง
      }
      break;

    case STATE_DELAY2:  // รอเมื่อหมุนไป angleStart
      if (currentTime - newServoStateStartTime >= delayTime) {
        totalRounds--;  // ลบจำนวนรอบที่ทำสำเร็จแล้ว
        Serial.print("Round complete. Rounds remaining: ");
        Serial.println(totalRounds);

        if (totalRounds > 0) {
          // ถ้ายังเหลือรอบให้ทำต่อ
          newServo.write(angleEnd);  // เริ่มรอบใหม่
          newServoStateStartTime = currentTime;
          newServoState = STATE_DELAY1;
        } else {
          // ทำครบทุกรอบแล้ว
          newServoState = STATE_IDLE;  // กลับสู่สถานะนิ่ง
          Serial.println("Finished all rounds. Stopping at start angle.");
        }
      }
      break;

    default:
      break;
  }
}

// --- MODIFIED FUNCTION: New Servo Start Command (Non-Blocking) ---
void handleNewServoAndButton() {

  if (digitalRead(newButtonPin) == LOW) {  // ปุ่มกด
    sendToServer("pressed");
   
     if (newServoState == STATE_IDLE && totalRounds > 0) {

    // เริ่มต้นกระบวนการหมุนรอบแรก
    newServo.write(angleEnd);
    newServoStateStartTime = millis();
    newServoState = STATE_DELAY1;  // เข้าสู่สถานะรอหน่วงเวลาแรก

    Serial.print("Starting new servo for ");
    Serial.print(totalRounds);
    Serial.println(" rounds (Non-Blocking)...");
  }
   delay(500); // กันการส่งซ้ำเร็วเกินไป
  }
    else {
    sendToServer("released");
    delay(500);
  }
  // Check if the button is pressed (LOW), the servo is currently idle, and there are rounds to execute
  // เนื่องจาก totalRounds จะถูกลดค่าใน updateNewServo() ถ้าไม่ต้องการให้ทำงานตอน totalRounds เป็น 0 ต้อง check ตรงนี้
 
}

// ---------------------------------------------
// --- ORIGINAL FUNCTIONS (FOR COMPLETENESS) ---
// ---------------------------------------------

void handleUIState() {
  unsigned long currentTime = millis();
  if (needsRedraw) {
    switch (currentUIState) {
      case STATE_BOOT:
        showCyberpunkBoot();
        break;
      case STATE_CONNECTING_WIFI:
        showNetworkHacking();
        break;
      case STATE_CONNECTION_SUCCESS:
        showNetworkCompromised();
        break;
      case STATE_DASHBOARD:
        showCyberDashboard();
        break;
      case STATE_SHOW_MESSAGE:
        displayReceivedMessage(messageToShow);
        break;
    }
    needsRedraw = false;
  }

  switch (currentUIState) {
    case STATE_BOOT:
      if (currentTime - lastStateChangeTime > 3000) {
        setState(STATE_CONNECTING_WIFI);
      }
      break;

    case STATE_CONNECTING_WIFI:
      if (WiFi.status() == WL_CONNECTED) {
        setState(STATE_DASHBOARD);
      } else {
        static int retries = 0;
        static unsigned long lastUpdate = 0;
        if (currentTime - lastUpdate > 500) {
          updateHackingProgress(retries++);
          lastUpdate = currentTime;
        }
      }
      break;
    case STATE_CONNECTION_SUCCESS:
      if (currentTime - lastStateChangeTime > 4000) {
        setState(STATE_DASHBOARD);
      }
      break;

    case STATE_DASHBOARD:
      drawCyberStatusBar();
      break;
    case STATE_SHOW_MESSAGE:
      // Lock screen, no automatic state change
      break;
  }
}

void setState(UIState newState) {
  currentUIState = newState;
  lastStateChangeTime = millis();
  needsRedraw = true;
}

void initializeTFTDisplay() {
  tft.init();
  tft.setRotation(1);
  setState(STATE_BOOT);
}

void showCyberpunkBoot() {
  tft.fillScreen(COLOR_BACKGROUND);
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(5);
  tft.drawCentreString("NEURAL CORE", SCREEN_WIDTH / 2 + 4, 60 + 4, 1);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.drawCentreString("NEURAL CORE", SCREEN_WIDTH / 2, 60, 1);
  tft.setTextSize(3);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  int startY = 140;
  int lineSpacing = 35;
  tft.drawString("INITIALIZING...", 20, startY);
  tft.drawString("LOADING PATHWAYS...", 20, startY + lineSpacing);
  tft.drawString("ESTABLISHING LINK...", 20, startY + (2 * lineSpacing));
  drawCyberLoadingBar();
}

void drawCyberLoadingBar() {
  int barWidth = 440;
  int barHeight = 25;
  int barX = (SCREEN_WIDTH - barWidth) / 2;
  int barY = 270;
  tft.drawRoundRect(barX, barY, barWidth, barHeight, 5, COLOR_MATRIX_GREEN);
  for (int i = 0; i <= 100; i += 20) {
    int progressWidth = map(i, 0, 100, 0, barWidth - 4);
    if (progressWidth > 0) {
      tft.fillRect(barX + 2, barY + 2, progressWidth, barHeight - 4, COLOR_MATRIX_GREEN);
    }
    delay(50);
  }
}

void initializeServoMotors() {
  ESP32PWM::allocateTimer(0);
  ESP32PWM::allocateTimer(1);
  ESP32PWM::allocateTimer(2);
  ESP32PWM::allocateTimer(3);
  for (int i = 0; i < NUM_SERVOS; i++) {
    myServos[i].attach(servoPins[i], 500, 2500);
    myServos[i].write(0);
    currentServoAngles[i] = 0;
    targetServoAngles[i] = 0;
    isServoMoving[i] = false;
  }
}

void showNetworkHacking() {
  tft.fillScreen(COLOR_BACKGROUND);
  drawCyberHeader("NETWORK INFILTRATION");
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  int startY = 80;
  int lineSpacing = 35;
  tft.drawString(">>> SCANNING...", 20, startY);
  tft.drawString(">>> BYPASSING FIREWALL...", 20, startY + lineSpacing);
  tft.drawString(">>> INJECTING PAYLOAD...", 20, startY + (2 * lineSpacing));
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(3);
  tft.drawCentreString("TARGET: " + String(ssid), SCREEN_WIDTH / 2, 220, 1);
}

void updateHackingProgress(int attempt) {
  int barY = 270;
  int barWidth = 440;
  int barX = (SCREEN_WIDTH - barWidth) / 2;
  tft.drawRect(barX, barY, barWidth, 25, COLOR_MATRIX_GREEN);
  int progress = map(attempt, 0, 40, 0, barWidth - 4);
  if (progress > 0 && progress < barWidth - 4) {
    tft.fillRect(barX + 2, barY + 2, progress, 21, COLOR_MATRIX_GREEN);
  }
}

void showNetworkCompromised() {
  tft.fillScreen(COLOR_BACKGROUND);
  drawCyberHeader("ACCESS GRANTED");
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(4);
  tft.drawCentreString("NETWORK", SCREEN_WIDTH / 2 + 3, 80 + 3, 1);
  tft.drawCentreString("COMPROMISED", SCREEN_WIDTH / 2 + 3, 130 + 3, 1);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.drawCentreString("NETWORK", SCREEN_WIDTH / 2, 80, 1);
  tft.drawCentreString("COMPROMISED", SCREEN_WIDTH / 2, 130, 1);
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(3);
  tft.drawCentreString("IP: " + WiFi.localIP().toString(), SCREEN_WIDTH / 2, 220, 1);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(2);
  tft.drawCentreString("SYSTEM ONLINE", SCREEN_WIDTH / 2, 270, 1);
}

void showSecurityBreach(String message) {
  tft.fillScreen(COLOR_BACKGROUND);
  drawCyberHeader("SECURITY BREACH");
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(4);
  tft.drawCentreString(message, SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2 - 40, 1);
  tft.setTextSize(3);
  tft.drawCentreString("SYSTEM LOCKDOWN", SCREEN_WIDTH / 2, SCREEN_HEIGHT / 2 + 20, 1);
}

void showCyberDashboard() {
  tft.fillScreen(COLOR_BACKGROUND);
  drawCyberHeader("NEURAL INTERFACE");

  int margin = 0;
  int gap = 10;
  int panelWidth = (SCREEN_WIDTH - gap) / 2;
  int panelHeight = 130;
  // System Status Panel
  drawCyberPanel(margin, 50, panelWidth, panelHeight, "CORE STATUS");
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  tft.drawString("SYS: ONLINE", margin + 10, 85);
  tft.drawString("IR: " + String(count), margin + 10, 120);
  tft.drawString("TEMP: 42C", margin + 10, 155);
  // Network Panel
  int networkPanelX = margin + panelWidth + gap;
  drawCyberPanel(networkPanelX, 50, panelWidth, panelHeight, "NETWORK");
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(2);
  tft.drawString("WLAN: CONNECTED", networkPanelX + 10, 85);
  tft.drawString("IP: " + WiFi.localIP().toString(), networkPanelX + 10, 115);
  tft.drawString("SERVER: ACTIVE", networkPanelX + 10, 145);
  // Servo Matrix
  drawCyberPanel(margin, 190, SCREEN_WIDTH, 90, "SERVO MATRIX");
  updateServoStatusDisplay();
}

void updateServoStatusDisplay() {
  int margin = 0;
  tft.fillRect(margin + 5, 220, SCREEN_WIDTH - 10, 60, COLOR_DARK_PANEL);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  for (int i = 0; i < NUM_SERVOS; i++) {
    String status = "S" + String(i + 1) + ":" + String(currentServoAngles[i]) + "d";
    int colX = (margin + 15) + (i * (SCREEN_WIDTH / NUM_SERVOS));
    if (i > 2) {
      colX = (margin + 15) + ((i - 3) * (SCREEN_WIDTH / (NUM_SERVOS - 2)));
    }
    int rowY = (i < 3) ? 225 : 255;
    tft.drawString(status, colX, rowY);
  }
}

void setupWebServer() {
  for (int i = 0; i < NUM_SERVOS; i++) {
    String pathOn = "/servo" + String(servoPins[i]) + "/on";
    String pathOff = "/servo" + String(servoPins[i]) + "/off";
    server.on(pathOn.c_str(), HTTP_GET, [=]() {
      handleServo(i, HIGH);
    });
    server.on(pathOff.c_str(), HTTP_GET, [=]() {
      handleServo(i, LOW);
    });
  }

  server.on("/", HTTP_GET, handleRoot);
  server.onNotFound(handleNotFound);
  server.on("/oled", HTTP_POST, []() {
    messageToShow = server.arg("plain");
    setState(STATE_SHOW_MESSAGE);
    server.send(200, "text/plain", "Neural interface updated");
  });
  server.begin();
  Serial.println("HTTP server started.");
}

void displayReceivedMessage(String message) {
  tft.fillScreen(COLOR_BACKGROUND);
  drawCyberHeader("INCOMING TRANSMISSION");
  int panelX = 0;
  int panelW = SCREEN_WIDTH;
  int panelY = 60;
  int panelH = 240;

  drawCyberPanel(panelX, panelY, panelW, panelH, "DECRYPTED DATA");
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  int cursorX = panelX + 10;
  int cursorY = panelY + 40;
  int lineHeight = 30;
  int maxLines = (panelH - 60) / lineHeight;
  int currentPos = 0;
  int lineNum = 0;

  while (currentPos < message.length() && lineNum < maxLines) {
    String line = "";
    int lastSpace = -1;
    int lineEnd = currentPos;
    while (lineEnd < message.length()) {
      if (message.charAt(lineEnd) == ' ') lastSpace = lineEnd;
      if (message.charAt(lineEnd) == '\n' || tft.textWidth(message.substring(currentPos, lineEnd + 1), 1) > (panelW - 20)) {
        break;
      }
      lineEnd++;
    }
    if (lineEnd < message.length() && message.charAt(lineEnd) != '\n' && lastSpace != -1) {
      line = message.substring(currentPos, lastSpace);
      currentPos = lastSpace + 1;
    } else {
      line = message.substring(currentPos, lineEnd);
      currentPos = lineEnd;
      if (currentPos < message.length() && message.charAt(currentPos) == '\n') {
        currentPos++;
      }
    }
    tft.drawString(line, cursorX, cursorY);
    cursorY += lineHeight;
    lineNum++;
  }
}

void handleIRSensor() {
  int sensorValue = digitalRead(IR_SENSOR_PIN);
  if (sensorValue == LOW && !objectDetected) {
    count++;
    totalRounds++;  // Increment the round count for the new servo
    objectDetected = true;
    if (currentUIState == STATE_DASHBOARD) {
      updateCounterDisplay();
    }
    Serial.print("Count: ");
    Serial.println(count);
    Serial.print("Total rounds for new servo: ");
    Serial.println(totalRounds);
    Serial.println("Sensor triggered! Sending reset command to Flask.");
    sendResetCommandToFlask();
  } else if (sensorValue == HIGH && objectDetected) {
    objectDetected = false;
  }
}

void updateCounterDisplay() {
  int margin = 0;
  int x = (margin + 10) + tft.textWidth("IR: ", 1);
  int y = 120;
  int w = tft.textWidth("999", 1);
  int h = tft.fontHeight(1);
  tft.fillRect(x, y, w, h, COLOR_DARK_PANEL);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  tft.drawString(String(count), x, y);
}

void drawCyberHeader(String title) {
  tft.fillRect(0, 0, SCREEN_WIDTH, 40, COLOR_DARK_PANEL);
  tft.drawFastHLine(0, 39, SCREEN_WIDTH, COLOR_MATRIX_GREEN);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.setTextSize(3);
  tft.drawCentreString(title, SCREEN_WIDTH / 2, 10, 1);
  tft.drawString(">", 2, 10);
  tft.drawString("<", SCREEN_WIDTH - 20, 10);
}

void drawCyberPanel(int x, int y, int w, int h, String title) {
  tft.fillRoundRect(x, y, w, h, 8, COLOR_DARK_PANEL);
  tft.drawRoundRect(x, y, w, h, 8, COLOR_MATRIX_GREEN);
  tft.fillRect(x + 5, y + 5, w - 10, 25, COLOR_MATRIX_GREEN);
  tft.setTextColor(COLOR_BACKGROUND);
  tft.setTextSize(2);
  tft.drawCentreString(title, x + w / 2, y + 9, 1);
}

void drawCyberStatusBar() {
  int barY = SCREEN_HEIGHT - 30;
  tft.fillRect(0, barY, SCREEN_WIDTH, 30, COLOR_DARK_PANEL);
  tft.drawFastHLine(0, barY, SCREEN_WIDTH, COLOR_MATRIX_GREEN);
  tft.setTextColor(COLOR_CYBER_RED);
  tft.setTextSize(2);
  String timeStr = "T+" + String(millis() / 1000) + "s";
  tft.drawString(timeStr, SCREEN_WIDTH - tft.textWidth(timeStr, 1) - 2, barY + 8);
  tft.setTextColor(COLOR_MATRIX_GREEN);
  tft.drawString("STATUS: NOMINAL", 2, barY + 8);
}

void handleRoot() {
  String html = "<html><body><h1>ESP32 Servo Control</h1>";
  html += "<p>Connected to WiFi: " + String(ssid) + "</p>";
  html += "<p>IP Address: " + WiFi.localIP().toString() + "</p>";
  html += "<h2>Control Servos:</h2><ul>";
  for (int i = 0; i < NUM_SERVOS; i++) {
    html += "<li>Servo on GPIO " + String(servoPins[i]) + ": ";
    html += "<a href='/servo" + String(servoPins[i]) + "/on'>ON (60 deg)</a> | ";
    html += "<a href='/servo" + String(servoPins[i]) + "/off'>OFF (0 deg)</a>";
    html += "</li>";
  }
  html += "</ul></body></html>";
  server.send(200, "text/html", html);
}

void handleServo(int servoIndex, int state) {
  int targetAngle = (state == HIGH) ? 60 : 0;
  String stateStr = (state == HIGH) ? "ON (60 degrees)" : "OFF (0 degrees)";

  targetServoAngles[servoIndex] = targetAngle;
  servoMoveStartTime[servoIndex] = millis();
  isServoMoving[servoIndex] = true;

  Serial.printf("Servo on GPIO %d command: %s\n", servoPins[servoIndex], stateStr.c_str());
  server.send(200, "text/plain", "Servo " + String(servoPins[servoIndex]) + " command: " + stateStr);
}

void updateServoPositions() {
  unsigned long currentTime = millis();
  bool servoStateChanged = false;
  for (int i = 0; i < NUM_SERVOS; i++) {
    if (isServoMoving[i]) {
      unsigned long elapsedTime = currentTime - servoMoveStartTime[i];
      if (elapsedTime < SERVO_MOVE_DURATION) {
        float progress = (float)elapsedTime / SERVO_MOVE_DURATION;
        int startAngle = currentServoAngles[i];
        int newAngle = startAngle + (int)((targetServoAngles[i] - startAngle) * progress);
        myServos[i].write(newAngle);
      } else {
        myServos[i].write(targetServoAngles[i]);
        currentServoAngles[i] = targetServoAngles[i];
        isServoMoving[i] = false;
        servoStateChanged = true;
        Serial.printf("Servo on GPIO %d reached target angle: %d\n", servoPins[i], targetServoAngles[i]);
      }
    }
  }
  if (servoStateChanged && currentUIState == STATE_DASHBOARD) {
    updateServoStatusDisplay();
  }
}

void handleNotFound() {
  String message = "File Not Found\n\nURI: " + server.uri();
  server.send(404, "text/plain", message);
}

void sendResetCommandToFlask() {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    String serverPath = "http://" + String(flask_ip) + ":6003/sensor_reset";
    http.begin(serverPath.c_str());
    int httpResponseCode = http.GET();
    if (httpResponseCode > 0) {
      Serial.printf("[HTTP] GET... code: %d\n", httpResponseCode);
      String payload = http.getString();
      Serial.println("[HTTP] Response payload:");
      Serial.println(payload);
    } else {
      Serial.printf("[HTTP] GET... failed, error: %s\n", http.errorToString(httpResponseCode).c_str());
    }

    http.end();
  } else {
    Serial.println("WiFi not connected, cannot send reset command.");
  }
}




void sendToServer(String state) {
  if(WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    String serverPath = "http://" + String(flask_ip) + ":6003/update_button";
    http.begin(serverPath.c_str());
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