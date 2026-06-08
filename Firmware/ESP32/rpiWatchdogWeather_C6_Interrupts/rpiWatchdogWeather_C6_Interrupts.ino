#include <Adafruit_NeoPixel.h>
#include "esp_sleep.h"
#include "esp_task_wdt.h"


#define IMAGE_SENT_SIGNAL_FROM_RPI          0   // GPIO23 on RPi
#define SEND_SHUTDOWN_SIGNAL_TO_RPI         1   // GPIO24 on RPi
#define SHUTDOWN_COMPLETE_STATUS_FROM_RPI   2   // GPIO25 on RPi
#define RELAY_PIN                           3   // CH1 of Relay


const int adcPin = 5;
// Measured voltage divider resistors
const float R1 = 9820.0;
const float R2 = 3630.0;

// Calibration point (measured with multimeter)
const int ADC_CALIB = 3366;
const float V_CALIB = 3.37;
const int NUM_SAMPLES = 10;  // number of ADC samples to average
float slope = V_CALIB / ADC_CALIB;  // ADC to voltage scale factor
float Vbat = 0.0;

const unsigned long PI_TIMEOUT              = 8 * 60 * 1000;             // 8 mins in milliseconds
const unsigned long WDT_TIMEOUT             = 2  * 60 * 1000;            // 2 mins in milliseconds
const unsigned long SHUTDOWN_TIMEOUT        = 3  * 60 * 1000;            // 3 mins in milliseconds
const unsigned long SLEEP_TIME_BATTERY_DIE  = 60 * 60 * 1000000;         // 1 hour in microseconds

unsigned long SLEEP_TIME              = 20 * 60 * 1000000;              // 20 mins in microseconds, can be changed based on the battery

unsigned long PiStartTime = 0;
unsigned long shutdownStart = 0;


//Adafruit_NeoPixel strip(1, 8, NEO_GRB + NEO_KHZ800);

volatile bool imageSentFlag = false;
void IRAM_ATTR imageSentISR() 
{
  imageSentFlag = true;
}

esp_task_wdt_config_t config = 
{
  .timeout_ms = WDT_TIMEOUT, 
  .trigger_panic = true,
};



void setup() 
{
  Serial.begin(115200);
  Serial0.begin(9600);

  pinMode(IMAGE_SENT_SIGNAL_FROM_RPI, INPUT_PULLDOWN);
  pinMode(SEND_SHUTDOWN_SIGNAL_TO_RPI, OUTPUT);
  pinMode(SHUTDOWN_COMPLETE_STATUS_FROM_RPI, INPUT_PULLDOWN);
  pinMode(RELAY_PIN, OUTPUT);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  Vbat = readBatteryVoltage();
  delay(1000);

  if (Vbat >= 9)
  {
    digitalWrite(RELAY_PIN, LOW);
    delay(2000);
    digitalWrite(RELAY_PIN, HIGH);
  }
  else
  {
    esp_sleep_enable_timer_wakeup(SLEEP_TIME_BATTERY_DIE);
    esp_deep_sleep_start();
  } 

  unsigned long waitStart = millis();
  String msg = "";

  // Wait up to 2 mins for a full line
  while (millis() - waitStart < 120000) 
  {
    if (Serial0.available()) 
    {
      msg = Serial0.readStringUntil('\n');
      msg.trim();
      break;
    }
    delay(50);
  }

  if (msg == "SEND VOLTAGE") 
  {
    Serial0.println(Vbat);
  } 
  else 
  {
    Serial.println("No valid command from RPi about battery.");
  }
  
  esp_task_wdt_deinit();
  esp_task_wdt_init(&config);
  esp_task_wdt_add(NULL);

  PiStartTime = millis();
  attachInterrupt(digitalPinToInterrupt(IMAGE_SENT_SIGNAL_FROM_RPI), imageSentISR, RISING);
}

void loop() 
{
  checkPiTimeout();
  esp_task_wdt_reset();
  delay(100);
  
  if(imageSentFlag) 
  {
    Serial.println("Interrupt Detected");
    esp_task_wdt_reset();

    imageSentFlag = false;
    PiStartTime = millis();

    Serial.println("Image Sent to server! Sending shutdown command to Pi.");
    digitalWrite(SEND_SHUTDOWN_SIGNAL_TO_RPI, HIGH);

    shutdownStart = millis();
    // Wait for Pi shutdown complete
    while (digitalRead(SHUTDOWN_COMPLETE_STATUS_FROM_RPI) != LOW) 
    {
      esp_task_wdt_reset();
      Serial.println("Waiting for Pi to shutdown");
      if (millis() - shutdownStart > SHUTDOWN_TIMEOUT)
      {
        Serial.println("Pi shutdown timeout, proceeding anyway.");
        break;
      }
    }

    Serial.println("Pi shutdown confirmed / timeout reached");
    digitalWrite(SEND_SHUTDOWN_SIGNAL_TO_RPI, LOW);
    delay(500);

    digitalWrite(RELAY_PIN, LOW);
    delay(500);

    if (Vbat<11 && Vbat>9)
    {
      SLEEP_TIME = 30*60*1000000;
    }
    esp_sleep_enable_timer_wakeup(SLEEP_TIME);
    esp_deep_sleep_start();
  }
}


// --- Check Pi timeout ---
void checkPiTimeout() 
{
  if (millis() - PiStartTime > PI_TIMEOUT) 
  {
      Serial.println("Pi timeout! Power cycling...");
      digitalWrite(RELAY_PIN, LOW);
      delay(2000);
      digitalWrite(RELAY_PIN, HIGH);
      delay(2000);

      PiStartTime = millis();
  }
}



float readBatteryVoltage() 
{
  long sum = 0;
  for (int i = 0; i < NUM_SAMPLES; i++) {
    sum += analogRead(adcPin);
    delay(5); // small delay between samples
  }
  float adcAverage = sum / (float)NUM_SAMPLES;

  // Voltage at ADC pin
  float voltageAtPin = adcAverage * slope;

  // Calculate actual battery voltage
  float batteryVoltage = voltageAtPin * ((R1 + R2) / R2);

  return batteryVoltage;
}




