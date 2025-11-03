// กำหนดขา PWM ESP32
const int AIN1 = 25; // GPIO25
const int AIN2 = 26; // GPIO26

// PWM settings
// const int freq = 1000;        // ความถี่ 1kHz
// const int pwmChannel1 = 0;    // channel 0
// const int pwmChannel2 = 1;    // channel 1
// const int resolution = 8;     // 8-bit PWM (0-255)

void setup() {
  // ตั้งค่า PWM channels
  //ledcSetup(pwmChannel1, freq, resolution);
  //ledcSetup(pwmChannel2, freq, resolution);

  // ต่อ GPIO เข้ากับ channel
 
}

void loop() {
  // หมุนไปข้างหน้า 50%
  //ledcWrite(pwmChannel1, 128);  // AIN1
  //ledcWrite(pwmChannel2, 0);    // AIN2
  analogWrite(AIN1,255);
  analogWrite(AIN2,0);
  delay(2000);
 analogWrite(AIN1,0);
  analogWrite(AIN2,255);
  delay(2000);
  // หมุนย้อนกลับ 100%
  //ledcWrite(pwmChannel1, 0);    // AIN1
  // ledcWrite(pwmChannel2, 255);  // AIN2
  // delay(2000);

  // // หยุด
  // ledcWrite(pwmChannel1, 0);    // AIN1
  // ledcWrite(pwmChannel2, 0);    // AIN2
  // delay(2000);
}
