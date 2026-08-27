#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include "esp_sleep.h"
#include "esp_task_wdt.h"

#define IMAGE_SENT_SIGNAL_FROM_RPI          0   // GPIO23 on RPi
#define SEND_SHUTDOWN_SIGNAL_TO_RPI         1   // GPIO24 on RPi
#define SHUTDOWN_COMPLETE_STATUS_FROM_RPI   2   // GPIO25 on RPi
#define BATTERY_PIN                         5   // Battery monitoring pin
#define RPI_ENABLE                          14  // To enable power for RPi
 
const char* apSSID = "ESP32-CAMERA-CONFIG";
const char* apPassword = "12345678";

bool          lowBattery        = false;
bool          checkPiStatus     = false;
bool          configMode        = false;
volatile bool imageSentFlag     = false;
static float  Vbat              = 0.0;
unsigned long PiStartTime       = 0;
unsigned long shutdownStart     = 0;
unsigned long configStartMillis = 0;
RTC_DATA_ATTR bool skipConfigPortal = false;    // should be false



const uint32_t PI_TIMEOUT              = 8UL  * 60 * 1000;          // 8 mins    
const uint32_t WDT_TIMEOUT             = 2UL  * 60 * 1000;         // 2 mins      
const uint32_t CONFIG_TIMEOUT_MS       = 3UL  * 60 * 1000;        // 3 mins
const uint32_t SHUTDOWN_TIMEOUT        = 3UL  * 60 * 1000;        // 3 mins 
const uint64_t SLEEP_TIME_BATTERY_DIE  = 120UL * 60 * 1000000;    // 120 mins  
uint64_t       SLEEP_TIME              = 20UL * 60 * 1000000;   // 20 mins, will also be fetched by webserver of esp and will be initilaised in function startNormalMode
uint8_t        PI_RESTARTS             = 0;

// Global Config Values
String cameraId, location, ftpHost, ftpUser, ftpPass, ftpPath = "/", protocol = "ftp";
uint32_t ftpPort = 21;
uint32_t frameRate = 20;    // 20 mins
uint32_t newFrameRate = 20;  // getting it from server (RPi)

WebServer server(80);
Preferences prefs;

void shutdownAndSleep(String reason);
void checkPiTimeout();
float readBatteryRaw();

void IRAM_ATTR imageSentISR() 
{
  imageSentFlag = true;
}

esp_task_wdt_config_t wdtConfig = 
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
  protocol = prefs.getString("protocol", "ftp");
  frameRate = prefs.getUInt("frameRate", 20);
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
  String protocol = prefs.getString("protocol", "ftp");
  uint32_t ftpPort = prefs.getUInt("ftpPort", 21);
  uint32_t frameRate = prefs.getUInt("frameRate", 20);
  
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

    Protocol:<br>
    <select name="protocol" required>
      <option value="ftp" %FTP_SELECTED%>FTP</option>
      <option value="sftp" %SFTP_SELECTED%>SFTP</option>
    </select><br><br>


    FTP Username:<br>
    <input name="ftpUser" value="%FTP_USER%"><br><br>
    FTP Password:<br>
    <input name="ftpPass" type="password" placeholder="Leave blank to keep current password"><br><br>

    FTP Path:<br>
    <input name="ftpPath" value="%FTP_PATH%"><br><br>

    Image Sending Frequency (mins): <br>
    <input name="frameRate" value="%FRAME_RATE%"><br><br>
    
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
  page.replace("%FTP_SELECTED%", protocol == "ftp" ? "selected" : "");
  page.replace("%SFTP_SELECTED%", protocol == "sftp" ? "selected" : "");
  page.replace("%FRAME_RATE%", String(frameRate));
  
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
  String newProtocol = server.arg("protocol");
  String newFrameRate = server.arg("frameRate");

  newCameraId.trim();
  newLocation.trim();
  newFtpHost.trim();
  newFtpPort.trim();
  newFtpUser.trim();
  newFtpPass.trim();
  newFtpPath.trim();
  newFrameRate.trim();
  newProtocol.trim();
  newProtocol.toLowerCase();

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

  if (newProtocol != "ftp" && newProtocol != "sftp")
  {
    server.send(400, "text/html", "<h2>Protocol must be ftp or sftp.</h2>");
    return;
  }


  prefs.putString("cameraId", newCameraId);
  prefs.putString("location", newLocation);
  prefs.putString("ftpHost", newFtpHost);
  prefs.putUInt("ftpPort", newPort);
  prefs.putString("ftpUser", newFtpUser);
  prefs.putString("ftpPath", newFtpPath);
  prefs.putString("protocol", newProtocol);

  if (newFtpPass.length() > 0) 
  {
    prefs.putString("ftpPass", newFtpPass);
  }

  if (newFrameRate.toInt() <= 0 || newFrameRate.toInt() > 1440)
  {
    server.send(400, "text/html", "<h2>Frame rate must be 1..1440 minutes.</h2>");
    return;
  }
  prefs.putUInt("frameRate", newFrameRate.toInt());
  
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
  WiFi.softAP(apSSID, apPassword, 1, false, 1); // only 1 client connection

  Serial.println("Configuration mode started");
  Serial.print("Open: http://");
  Serial.println(WiFi.softAPIP());

  server.on("/", HTTP_GET, handleRoot);
  server.on("/save", HTTP_POST, handleSave);
  server.begin();

  configMode = true;
  configStartMillis = millis();
}

void startNormalMode() 
{
  if (!isConfigured()) 
  {
    Serial.println("No valid configuration. Normal mode not started.");
    return;
  }

  loadConfig();
  SLEEP_TIME = (uint64_t)frameRate * 60 * 1000000; // FrameRate in milliseconds

  Serial.println("Normal mode started");
  Serial.println("Camera ID: " + cameraId);
  Serial.println("Location: " + location);
  Serial.println("FTP Host: " + ftpHost);
  Serial.println("FTP Port: " + String(ftpPort));
  Serial.println("FTP User: " + ftpUser);
  Serial.println("FTP Path: " + ftpPath);
  Serial.println("FTP Password length: " + String(ftpPass.length()));
  Serial.println("Protocol: " + protocol);
  Serial.println("Frame Rate: " + String(frameRate));
}

void setup() 
{
  Serial.begin(115200);
  Serial0.begin(9600);
  Serial0.setTimeout(100);
  
  pinMode(IMAGE_SENT_SIGNAL_FROM_RPI, INPUT_PULLDOWN);
  pinMode(SEND_SHUTDOWN_SIGNAL_TO_RPI, OUTPUT);
  pinMode(SHUTDOWN_COMPLETE_STATUS_FROM_RPI, INPUT_PULLDOWN);
  pinMode(RPI_ENABLE, OUTPUT);
  
  analogReadResolution(12);
  delay(1000);
  float raw = readBatteryRaw();
  Vbat = (0.006161 * raw) - 6.11;
  Serial.print("Raw = "); Serial.println(raw);
  Serial.print("Battery Voltage = "); Serial.println(Vbat);


  if (Vbat < 9.65 && Vbat >= 9.60)                                                  // Checking battery levels
  {
    Serial.println("Battery too low, Turning on RPi just to send low battery information");
    lowBattery = true;
  }
  else if (Vbat < 9.60)
  {
    Serial.print("Going into deep sleep for");
    Serial.println(SLEEP_TIME_BATTERY_DIE);
    esp_sleep_enable_timer_wakeup(SLEEP_TIME_BATTERY_DIE);
    delay(100);
    esp_deep_sleep_start();
  }


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

  digitalWrite(RPI_ENABLE, LOW);
  delay(2000);
  digitalWrite(RPI_ENABLE, HIGH);

  esp_task_wdt_deinit();
  esp_task_wdt_init(&wdtConfig);
  esp_task_wdt_add(NULL);

  PiStartTime = millis();
  attachInterrupt(digitalPinToInterrupt(IMAGE_SENT_SIGNAL_FROM_RPI), imageSentISR, RISING);
}

void loop() 
{
  checkPiTimeout();
  esp_task_wdt_reset();
  
  if (digitalRead(SHUTDOWN_COMPLETE_STATUS_FROM_RPI) == HIGH)
  {
    checkPiStatus = true;
  }
  
  if (checkPiStatus && (digitalRead(SHUTDOWN_COMPLETE_STATUS_FROM_RPI) == LOW ))
  {
    if (lowBattery)
    {
      shutdownAndSleep("RPi shutdown due to low battery");
    }
    else
    {
      shutdownAndSleep("RPi shutdown due to Error/Exception");
    }
  }

  if(Serial0.available())
  {
    esp_task_wdt_reset();
    PiStartTime = millis();                         // Resetting Pi Timeout

    String cmd = Serial0.readStringUntil('\n');
    cmd.trim();

    if (cmd == "SEND VOLTAGE")
    {
      Serial.println("Sending Voltage");
      Serial0.println(Vbat);
    }

    if (cmd == "SEND CONFIG" || cmd == "SEND CONFIG FORCED")
    {
      Serial.println(cmd);
      loadConfig();
      bool forceSend = (cmd == "SEND CONFIG FORCED");

      if (forceSend || prefs.getBool("configChanged", false))        // checking for configChanged variable, if it doesn't exist mark it as false
      {
        Serial.println(forceSend ? "Force sending config" : "Sending Config");
        Serial0.print(cameraId); Serial0.print("|");
        Serial0.print(location); Serial0.print("|");
        Serial0.print(ftpHost);  Serial0.print("|");
        Serial0.print(ftpPort);  Serial0.print("|");
        Serial0.print(ftpUser);  Serial0.print("|");
        Serial0.print(ftpPass);  Serial0.print("|");
        Serial0.print(ftpPath);  Serial0.print("|");
        Serial0.print(protocol); Serial0.print("|");
        Serial0.println(frameRate);
      }
      else
      {
        Serial.println("No new config");
        Serial0.println("NO NEW CONFIG");
      }
    }

    if (cmd == "CONFIG SAVED")
    {
      Serial.println("Configurations Saved in RPi");
      prefs.putBool("configChanged", false);            // marking it false again, so esp knows that configurations are not changed on next run
    }

    if (cmd.startsWith("SET FRAMERATE"))
    {
      newFrameRate = cmd.substring(14).toInt();
      if (newFrameRate > 0 && newFrameRate!= frameRate)
      {
        frameRate = newFrameRate;
        prefs.putUInt("frameRate", newFrameRate);
        SLEEP_TIME = (uint64_t) newFrameRate * 60 * 1000000;
        Serial0.println("FRAMERATE UPDATED");

        Serial.print("Frame Rate updated to (mins): "); Serial.println(newFrameRate);
        
      }
      else if (newFrameRate == frameRate)
      {
        Serial0.println("FRAMERATE IDENTICAL TO SAVED FRAME RATE");
        Serial.println("Same Frame Rate");
      }
      else
      {
        Serial0.println("FRAMERATE INVALID");
        Serial.print("Invalid frame rate received");
      }
    }

    esp_task_wdt_reset();
    PiStartTime = millis(); 
  }

  if(imageSentFlag) 
  {
    Serial.println("Interrupt Detected");
    esp_task_wdt_reset();

    imageSentFlag = false;
    PiStartTime = millis();

    Serial.println("Image Sent to server! Sending shutdown command to Pi.");
    digitalWrite(SEND_SHUTDOWN_SIGNAL_TO_RPI, HIGH);
    shutdownAndSleep("RPi shutting down after Image Successfully Sent");
  }
}












// --- Wait for RPi shutdown confirmation, then power off and sleep ---
void shutdownAndSleep(String reason)
{
  shutdownStart = millis();
  unsigned long lowSince = 0;

  while (true)
  {
    esp_task_wdt_reset();
    int pin = digitalRead(SHUTDOWN_COMPLETE_STATUS_FROM_RPI);

    if (pin == LOW)
    {
      if (lowSince == 0)
      {
        lowSince = millis();
      }
      if (millis() - lowSince >= 200)
      {
        break;
      }
    }
    else
    {
      lowSince = 0;
    }

    if (millis() - shutdownStart > SHUTDOWN_TIMEOUT)
    {
      Serial.println("Pi shutdown timeout, forcing power off.");
      break;
    }
    delay(10);
  }

  Serial.print("Pi shutdown down:");  Serial.println(reason);
  digitalWrite(SEND_SHUTDOWN_SIGNAL_TO_RPI, LOW);
  delay(500);

  digitalWrite(RPI_ENABLE, LOW);
  delay(500);

  skipConfigPortal = true;
  Serial.println("ESP going to deep sleep:");

  if (lowBattery)
  {
    Serial.print("Low Battery.Sending Data to Server and  Sleeping for ")
    Serial.println(SLEEP_TIME_BATTERY_DIE);
    esp_sleep_enable_timer_wakeup(SLEEP_TIME_BATTERY_DIE);
    delay(100);
    esp_deep_sleep_start();
  }
  else
  {
    Serial.println(SLEEP_TIME);
    esp_sleep_enable_timer_wakeup(SLEEP_TIME);
    delay(100);
    esp_deep_sleep_start();
  }
}











// --- Check Pi timeout ---
void checkPiTimeout() 
{
  if (millis() - PiStartTime > PI_TIMEOUT) 
  { 
    PI_RESTARTS++;
    Serial.println("Pi timeout! Power cycling...");
    digitalWrite(RPI_ENABLE, LOW);
    delay(2000);
    digitalWrite(RPI_ENABLE, HIGH);
    delay(2000);
    PiStartTime = millis();

    if (PI_RESTARTS >= 2)
    {
      Serial.println("Pi failed after max restarts. Forcing sleep.");
      shutdownAndSleep("Pi failed after max restarts. Forcing Sleep");
    }
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

