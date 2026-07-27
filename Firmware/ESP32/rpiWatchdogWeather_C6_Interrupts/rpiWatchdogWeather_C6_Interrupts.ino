#include <Adafruit_NeoPixel.h>
#include "esp_sleep.h"
#include "esp_task_wdt.h"

#define IMAGE_SENT_SIGNAL_FROM_RPI          0   // GPIO23 on RPi
#define SEND_SHUTDOWN_SIGNAL_TO_RPI         1   // GPIO24 on RPi
#define SHUTDOWN_COMPLETE_STATUS_FROM_RPI   2   // GPIO25 on RPi
#define RPI_ENABLE                          14  // To enable power for RPi

static String msg = "";
static float Vbat = 0.0;
bool Vbat_Sent = false;
bool FrameRate_Received = false;

const int BATTERY_PIN = 5;  
const uint64_t PI_TIMEOUT              = 8ULL * 60 * 1000;      // 8 mins      
const uint64_t WDT_TIMEOUT             = 2ULL * 60 * 1000;      // 2 mins      
const uint64_t SHUTDOWN_TIMEOUT        = 3ULL * 60 * 1000;      // 3 mins    
const uint64_t SLEEP_TIME              = 20ULL * 60 * 1000000;   // 20 mins 
const uint64_t SLEEP_TIME_BATTERY_DIE  = 60ULL * 60 * 1000000;  // 60 mins    

unsigned long PiStartTime = 0;
unsigned long shutdownStart = 0;

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
  pinMode(RPI_ENABLE, OUTPUT);
  
  analogReadResolution(12);
  float raw = readBatteryRaw();
  Vbat = (0.005806 * raw) - 5.262;
  Serial.print("  Battery Voltage = ");
  Serial.print(Vbat, 2);
  Serial.println(" V");

  if (Vbat >= 9)
  {
    digitalWrite(RPI_ENABLE, LOW);
    delay(2000);
    digitalWrite(RPI_ENABLE, HIGH);
  }
  else
  {
    esp_sleep_enable_timer_wakeup(SLEEP_TIME_BATTERY_DIE);
    esp_deep_sleep_start();
  } 

  esp_task_wdt_deinit();
  esp_task_wdt_init(&config);
  esp_task_wdt_add(NULL);

  PiStartTime = millis();
  attachInterrupt(digitalPinToInterrupt(IMAGE_SENT_SIGNAL_FROM_RPI), imageSentISR, RISING);
}

void loop() 
{
  esp_task_wdt_reset();
  if(Serial0.available())
  {
    String cmd = Serial0.readStringUntil('\n');
    cmd.trim();

    if (cmd == "SEND VOLTAGE" && !Vbat_Sent)
    {
      Serial.print("1st Data: ");
      Serial.println(cmd);
      Serial0.println(Vbat);
      Vbat_Sent = true;
    }
    else if (cmd.startsWith("Frame Rate:") && !FrameRate_Received)
    {
      Serial.print("2nd Data: ");
      Serial.println(cmd);
      Serial0.println("Frame Rate Received By ESP32 Successfully");
      FrameRate_Received = true;
    }

    esp_task_wdt_reset();
  }

  checkPiTimeout();
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
    Serial.println("Waiting for Pi to shutdown");
    while (digitalRead(SHUTDOWN_COMPLETE_STATUS_FROM_RPI) != LOW) 
    {
      esp_task_wdt_reset();
      if (millis() - shutdownStart > SHUTDOWN_TIMEOUT)
      {
        Serial.println("Pi shutdown timeout, proceeding anyway.");
        break;
      }
      delay(10);
    }

    Serial.println("Pi shutdown confirmed / timeout reached");
    digitalWrite(SEND_SHUTDOWN_SIGNAL_TO_RPI, LOW);
    delay(500);

    digitalWrite(RPI_ENABLE, LOW);
    delay(500);

    Serial.print("ESP going to deep sleep for ");
    Serial.println(SLEEP_TIME);
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
      digitalWrite(RPI_ENABLE, LOW);
      delay(2000);
      digitalWrite(RPI_ENABLE, HIGH);
      delay(2000);

      PiStartTime = millis();
  }
}


float readBatteryRaw() 
{
  long total = 0;
  const int samples = 100;

  for (int i = 0; i < samples; i++) {
    total += analogRead(BATTERY_PIN);
    delay(2);
  }

  return total / (float)samples;
}

float readBatteryVoltage() 
{
  float raw = readBatteryRaw();
  return (0.005806 * raw) - 5.262;
}
