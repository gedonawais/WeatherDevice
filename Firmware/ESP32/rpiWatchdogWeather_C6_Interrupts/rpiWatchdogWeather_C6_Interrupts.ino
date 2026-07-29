#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <Adafruit_NeoPixel.h>
#include "esp_sleep.h"
#include "esp_task_wdt.h"

#define IMAGE_SENT_SIGNAL_FROM_RPI          0   // GPIO23 on RPi
#define SEND_SHUTDOWN_SIGNAL_TO_RPI         1   // GPIO24 on RPi
#define SHUTDOWN_COMPLETE_STATUS_FROM_RPI   2   // GPIO25 on RPi
#define RPI_ENABLE                          14  // To enable power for RPi

const char* apSSID = "ESP32-CAMERA-CONFIG";
const char* apPassword = "12345678";

static float  Vbat              = 0.0;
bool          configMode        = false;
volatile bool Vbat_Sent         = false;
volatile bool imageSentFlag     = false;
unsigned long PiStartTime       = 0;
unsigned long shutdownStart     = 0;
unsigned long configStartMillis = 0;
RTC_DATA_ATTR bool skipConfigPortal = false;

const int      BATTERY_PIN = 5;  
const uint64_t PI_TIMEOUT              = 8ULL * 60 * 1000;      // 8 mins   us   
const uint64_t WDT_TIMEOUT             = 2ULL * 60 * 1000;      // 2 mins   us   
const uint64_t SHUTDOWN_TIMEOUT        = 3ULL * 60 * 1000;       // 3 mins  us
const uint64_t SLEEP_TIME              = 20ULL * 60 * 1000000;   // 20 mins us
const uint64_t SLEEP_TIME_BATTERY_DIE  = 60ULL * 60 * 1000000;  // 60 mins  us
const unsigned long CONFIG_TIMEOUT_MS  = 3UL   * 60 * 1000;   // 3 mins in milliseconds

// Global Config Values
String cameraId, location, ftpHost, ftpUser, ftpPass, ftpPath = "/";
uint32_t ftpPort = 21;

WebServer server(80);
Preferences prefs;


void IRAM_ATTR imageSentISR() 
{
  imageSentFlag = true;
}

esp_task_wdt_config_t config = 
{
  .timeout_ms = WDT_TIMEOUT, 
  .trigger_panic = true,
};

bool isConfigured() 
{
  return prefs.getBool("configured", false);
}

void loadConfig() 
{
  cameraId = prefs.getString("cameraId", "");
  location = prefs.getString("location", "");
  ftpHost  = prefs.getString("ftpHost", "");
  ftpPort  = prefs.getUInt("ftpPort", 21);
  ftpUser  = prefs.getString("ftpUser", "");
  ftpPass  = prefs.getString("ftpPass", "");
  ftpPath  = prefs.getString("ftpPath", "/");
}

void stopConfigPortal() 
{
  server.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  configMode = false;
  Serial.println("Configuration mode stopped.");
}

void handleRoot() 
{
  String cameraId = prefs.getString("cameraId", "");
  String location = prefs.getString("location", "");
  String ftpHost  = prefs.getString("ftpHost", "");
  String ftpUser  = prefs.getString("ftpUser", "");
  String ftpPath  = prefs.getString("ftpPath", "/");
  uint32_t ftpPort = prefs.getUInt("ftpPort", 21);

  String page = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ESP32 Camera Config</title>
</head>
<body>
  <h2>Camera Configuration</h2>
  <form method="POST" action="/save">
    Camera ID:<br>
    <input name="cameraId" value="%CAMERA_ID%" required><br><br>

    Location:<br>
    <input name="location" value="%LOCATION%"><br><br>

    FTP Host:<br>
    <input name="ftpHost" value="%FTP_HOST%" required><br><br>

    FTP Port:<br>
    <input name="ftpPort" type="number" value="%FTP_PORT%" required><br><br>

    FTP Username:<br>
    <input name="ftpUser" value="%FTP_USER%"><br><br>

    FTP Password:<br>
    <input name="ftpPass" type="password" placeholder="Leave blank to keep current password"><br><br>

    FTP Path:<br>
    <input name="ftpPath" value="%FTP_PATH%"><br><br>

    <button type="submit">Save</button>
  </form>
</body>
</html>
)rawliteral";

  page.replace("%CAMERA_ID%", cameraId);
  page.replace("%LOCATION%", location);
  page.replace("%FTP_HOST%", ftpHost);
  page.replace("%FTP_PORT%", String(ftpPort));
  page.replace("%FTP_USER%", ftpUser);
  page.replace("%FTP_PATH%", ftpPath);

  server.send(200, "text/html", page);
}

void handleSave() 
{
  String newCameraId = server.arg("cameraId");
  String newLocation = server.arg("location");
  String newFtpHost  = server.arg("ftpHost");
  String newFtpPort  = server.arg("ftpPort");
  String newFtpUser  = server.arg("ftpUser");
  String newFtpPass  = server.arg("ftpPass");
  String newFtpPath  = server.arg("ftpPath");

  newCameraId.trim();
  newLocation.trim();
  newFtpHost.trim();
  newFtpPort.trim();
  newFtpUser.trim();
  newFtpPass.trim();
  newFtpPath.trim();

  if (newCameraId.length() == 0 || newFtpHost.length() == 0 || newFtpPort.length() == 0) 
  {
    server.send(400, "text/html", "<h2>Camera ID, FTP Host and FTP Port are required.</h2>");
    return;
  }

  int newPort = newFtpPort.toInt();
  if (newPort <= 0 || newPort > 65535) 
  {
    server.send(400, "text/html", "<h2>Invalid FTP port.</h2>");
    return;
  }

  if (newFtpPath.length() == 0) 
  {
    newFtpPath = "/";
  }

  prefs.putString("cameraId", newCameraId);
  prefs.putString("location", newLocation);
  prefs.putString("ftpHost", newFtpHost);
  prefs.putUInt("ftpPort", newPort);
  prefs.putString("ftpUser", newFtpUser);
  prefs.putString("ftpPath", newFtpPath);

  if (newFtpPass.length() > 0) 
  {
    prefs.putString("ftpPass", newFtpPass);
  }

  prefs.putBool("configured", true);
  prefs.putBool("configChanged", true);

  loadConfig();

  server.send(200, "text/html", "<h2>Configuration saved.</h2><p>Using new configuration.</p>");
  delay(1000);

  stopConfigPortal();
}

void startConfigPortal() 
{
  WiFi.mode(WIFI_AP);
  WiFi.softAP(apSSID, apPassword);

  Serial.println("Configuration mode started");
  Serial.print("Open: http://");
  Serial.println(WiFi.softAPIP());

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();

  configMode = true;
  configStartMillis = millis();
}

bool startNormalMode() 
{
  if (!isConfigured()) 
  {
    Serial.println("No valid configuration. Normal mode not started.");
    return false;
  }

  loadConfig();

  Serial.println("Normal mode started");
  Serial.println("Camera ID: " + cameraId);
  Serial.println("Location: " + location);
  Serial.println("FTP Host: " + ftpHost);
  Serial.println("FTP Port: " + String(ftpPort));
  Serial.println("FTP User: " + ftpUser);
  Serial.println("FTP Path: " + ftpPath);
  Serial.println("FTP Password length: " + String(ftpPass.length()));

  return true;
}

void setup() 
{
  Serial.begin(115200);
  Serial0.begin(9600);
  
  pinMode(IMAGE_SENT_SIGNAL_FROM_RPI, INPUT_PULLDOWN);
  pinMode(SEND_SHUTDOWN_SIGNAL_TO_RPI, OUTPUT);
  pinMode(SHUTDOWN_COMPLETE_STATUS_FROM_RPI, INPUT_PULLDOWN);
  pinMode(RPI_ENABLE, OUTPUT);
  analogReadResolution(12);

  prefs.begin("cam-config", false);
  if (!skipConfigPortal)
  {
    startConfigPortal();
    while (configMode)
    {
      server.handleClient();
      if (millis() - configStartMillis >= CONFIG_TIMEOUT_MS)
      {
        Serial.println("Configuration timeout reached");
        stopConfigPortal();
      }
      delay(1);
    }
  }
  else
  {
    Serial.println("Skipping config portal after deep sleep");
  }
  startNormalMode();

  float raw = readBatteryRaw();
  Vbat = (0.005806 * raw) - 5.262;
  Serial.print("Battery Voltage = "); Serial.println(Vbat);
  
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
  if(Serial0.available())
  {
    esp_task_wdt_reset();
    String cmd = Serial0.readStringUntil('\n');
    cmd.trim();
    if (cmd == "SEND VOLTAGE")
    {
      Serial.println("Sending Voltage");
      Serial0.println(Vbat);
    }
    if (cmd == "SEND CONFIG")
    {
      if (prefs.getBool("configChanged", false))        // checking for configChanged variable, if it doesn't exist mark it as false
      {
        Serial.println("Sending Config");
        Serial0.print(cameraId); Serial0.print(",");
        Serial0.print(location); Serial0.print(",");
        Serial0.print(ftpHost);  Serial0.print(",");
        Serial0.print(ftpPort);  Serial0.print(",");
        Serial0.print(ftpUser);  Serial0.print(",");
        Serial0.print(ftpPass);  Serial0.print(",");
        Serial0.println(ftpPath);
      }
      else
      {
        Serial0.println("NO NEW CONFIG");
      }
    }
    if (cmd == "CONFIG SAVED")
    {
      prefs.putBool("configChanged", false);            // marking it false again, so esp knows that configurations are not changed on next run
    }
  }

  esp_task_wdt_reset();
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

    skipConfigPortal = true;
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
