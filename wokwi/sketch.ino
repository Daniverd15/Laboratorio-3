/*
 * sketch.ino  -  UNAB-Ambiental  (Laboratorio 2)
 * =====================================================================
 * ESP32 virtual (Wokwi) -> Azure IoT Central  via  DPS + MQTT/TLS.
 *
 * Sensores virtuales del diagrama (diagram.json):
 *   - DHT22          -> temperature (C) + humidity (% RH)   [GPIO 15]
 *   - Potenciometro  -> illuminance (lux, 0..1000)          [GPIO 34]
 *   - LED            -> responde al comando setAlertLed      [GPIO 13]
 *
 * Telemetria: temperature, humidity, illuminance   (>= 3 variables)
 * Comando   : setAlertLed  -> enciende/apaga el LED virtual.
 *
 * SEGURIDAD: rellena tus credenciales abajo SOLO para probar en Wokwi.
 *   NO subas este archivo con la Primary Key real al repositorio.
 *   En el repo deja los valores de ejemplo (config.h.example).
 *
 * Librerias (ver libraries.txt):
 *   PubSubClient, DHT sensor library, Adafruit Unified Sensor, ArduinoJson
 * =====================================================================
 */

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <time.h>
#include "mbedtls/md.h"
#include "mbedtls/base64.h"

// ----------------------- CONFIG (rellenar) ---------------------------
#define WIFI_SSID     "Wokwi-GUEST"
#define WIFI_PASS     ""                       // Wokwi-GUEST no lleva clave

#define ID_SCOPE      "0ne00XXXXXX"            // ID Scope de tu app IoT Central
#define DEVICE_ID     "esp32-aula-201"         // Device ID registrado
#define DEVICE_KEY    "PEGA_AQUI_TU_PRIMARY_KEY"   // Primary key (SAS) del dispositivo
// ---------------------------------------------------------------------

// Pines
#define DHT_PIN   15
#define DHT_TYPE  DHT22
#define POT_PIN   34        // ADC1 (solo entrada)
#define LED_PIN   13

// Intervalo de envio de telemetria (ms)
#define SEND_INTERVAL_MS  5000

const char* DPS_HOST = "global.azure-devices-provisioning.net";
const int   MQTT_PORT = 8883;

DHT dht(DHT_PIN, DHT_TYPE);
WiFiClientSecure net;
PubSubClient mqtt(net);

// Clave de dispositivo decodificada (base64 -> bytes) para el HMAC del SAS
uint8_t  decodedKey[64];
size_t   decodedKeyLen = 0;

// Buffers para respuestas DPS recibidas por el callback
volatile bool  msgArrived = false;
String         inTopic;
String         inPayload;

String assignedHub = "";
unsigned long lastSend = 0;
bool ledState = false;

// ---------------------------------------------------------------------
// Utilidades: URL-encode, HMAC-SHA256+Base64, token SAS
// ---------------------------------------------------------------------
String urlEncode(const String &src) {
  String out = "";
  const char *hex = "0123456789ABCDEF";
  for (size_t i = 0; i < src.length(); i++) {
    char c = src[i];
    if (('a' <= c && c <= 'z') || ('A' <= c && c <= 'Z') ||
        ('0' <= c && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~') {
      out += c;
    } else {
      out += '%';
      out += hex[(c >> 4) & 0xF];
      out += hex[c & 0xF];
    }
  }
  return out;
}

String hmacSha256Base64(const String &message) {
  uint8_t hmacResult[32];
  mbedtls_md_context_t ctx;
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_init(&ctx);
  mbedtls_md_setup(&ctx, info, 1);
  mbedtls_md_hmac_starts(&ctx, decodedKey, decodedKeyLen);
  mbedtls_md_hmac_update(&ctx, (const unsigned char *)message.c_str(), message.length());
  mbedtls_md_hmac_finish(&ctx, hmacResult);
  mbedtls_md_free(&ctx);

  unsigned char b64[64];
  size_t olen = 0;
  mbedtls_base64_encode(b64, sizeof(b64), &olen, hmacResult, 32);
  b64[olen] = 0;
  return String((char *)b64);
}

// resourceUri SIN codificar; genera "SharedAccessSignature sr=...&sig=...&se=..."
String createSasToken(const String &resourceUri, unsigned long expiry) {
  String encUri = urlEncode(resourceUri);
  String toSign = encUri + "\n" + String(expiry);
  String sig = hmacSha256Base64(toSign);
  String encSig = urlEncode(sig);
  return "SharedAccessSignature sr=" + encUri + "&sig=" + encSig + "&se=" + String(expiry);
}

unsigned long tokenExpiry() {
  time_t now = time(nullptr);
  return (unsigned long)now + 24UL * 3600UL;   // valido 24 h
}

// ---------------------------------------------------------------------
// Callback MQTT (sirve para DPS y para comandos del Hub)
// ---------------------------------------------------------------------
void onMqttMessage(char *topic, byte *payload, unsigned int len) {
  inTopic = String(topic);
  inPayload = "";
  for (unsigned int i = 0; i < len; i++) inPayload += (char)payload[i];
  msgArrived = true;

  // Comando directo desde IoT Central: $iothub/methods/POST/<nombre>/?$rid=NN
  if (inTopic.startsWith("$iothub/methods/POST/")) {
    handleDirectMethod(inTopic, inPayload);
  }
}

void handleDirectMethod(const String &topic, const String &payload) {
  // Extraer nombre del metodo y el rid
  int p1 = topic.indexOf("POST/") + 5;
  int p2 = topic.indexOf("/", p1);
  String method = topic.substring(p1, p2);
  int ridPos = topic.indexOf("$rid=");
  String rid = (ridPos >= 0) ? topic.substring(ridPos + 5) : "0";

  int status = 200;
  String result = "{\"result\":\"ok\"}";

  if (method == "setAlertLed") {
    String p = payload; p.toLowerCase();
    if (p.indexOf("true") >= 0 || p == "1")       ledState = true;
    else if (p.indexOf("false") >= 0 || p == "0") ledState = false;
    else                                          ledState = !ledState;   // toggle
    digitalWrite(LED_PIN, ledState ? HIGH : LOW);
    Serial.printf("[CMD] setAlertLed -> %s\n", ledState ? "ON" : "OFF");
    result = String("{\"result\":\"LED ") + (ledState ? "ON" : "OFF") + "\"}";
  } else {
    status = 404;
    result = "{\"result\":\"comando desconocido\"}";
  }

  String respTopic = "$iothub/methods/res/" + String(status) + "/?$rid=" + rid;
  mqtt.publish(respTopic.c_str(), result.c_str());
}

// Espera bloqueante a que llegue una respuesta DPS (o timeout)
bool waitForMessage(unsigned long timeoutMs) {
  unsigned long start = millis();
  msgArrived = false;
  while (!msgArrived && millis() - start < timeoutMs) {
    mqtt.loop();
    delay(20);
  }
  return msgArrived;
}

// ---------------------------------------------------------------------
// DPS: registra el dispositivo y devuelve el hostname del IoT Hub
// ---------------------------------------------------------------------
String runDps() {
  Serial.println("[DPS] Iniciando aprovisionamiento...");
  net.setInsecure();                 // Wokwi: sin validacion de CA (solo lab)
  mqtt.setServer(DPS_HOST, MQTT_PORT);
  mqtt.setBufferSize(2048);
  mqtt.setKeepAlive(60);
  mqtt.setCallback(onMqttMessage);

  String user = String(ID_SCOPE) + "/registrations/" + DEVICE_ID + "/api-version=2019-03-31";
  String sas  = createSasToken(String(ID_SCOPE) + "/registrations/" + DEVICE_ID, tokenExpiry());

  while (!mqtt.connected()) {
    Serial.println("[DPS] Conectando a DPS...");
    if (mqtt.connect(DEVICE_ID, user.c_str(), sas.c_str())) {
      Serial.println("[DPS] Conectado.");
    } else {
      Serial.printf("[DPS] Fallo rc=%d. Reintentando...\n", mqtt.state());
      delay(3000);
    }
  }

  mqtt.subscribe("$dps/registrations/res/#");
  mqtt.publish("$dps/registrations/PUT/iotdps-register/?$rid=1",
               (String("{\"registrationId\":\"") + DEVICE_ID + "\"}").c_str());

  String operationId = "";
  // Esperar respuesta inicial (status: assigning)
  if (waitForMessage(10000)) {
    StaticJsonDocument<1024> doc;
    if (!deserializeJson(doc, inPayload)) {
      operationId = String((const char *)(doc["operationId"] | ""));
    }
  }

  // Sondear estado hasta "assigned"
  for (int i = 0; i < 10 && assignedHub == ""; i++) {
    delay(2000);
    String getTopic = "$dps/registrations/GET/iotdps-get-operationstatus/?$rid=2&operationId=" + operationId;
    mqtt.publish(getTopic.c_str(), "{}");
    if (waitForMessage(10000)) {
      StaticJsonDocument<1024> doc;
      if (!deserializeJson(doc, inPayload)) {
        String status = String((const char *)(doc["status"] | ""));
        Serial.printf("[DPS] status=%s\n", status.c_str());
        if (status == "assigned") {
          assignedHub = String((const char *)doc["registrationState"]["assignedHub"]);
        } else if (doc["operationId"]) {
          operationId = String((const char *)doc["operationId"]);
        }
      }
    }
  }

  mqtt.disconnect();
  if (assignedHub == "") {
    Serial.println("[DPS] ERROR: no se obtuvo assignedHub.");
  } else {
    Serial.printf("[DPS] assignedHub = %s\n", assignedHub.c_str());
  }
  return assignedHub;
}

// ---------------------------------------------------------------------
// Conexion al IoT Hub asignado
// ---------------------------------------------------------------------
void connectHub() {
  mqtt.setServer(assignedHub.c_str(), MQTT_PORT);
  String user = assignedHub + "/" + DEVICE_ID + "/?api-version=2020-09-30";
  String sas  = createSasToken(assignedHub + "/devices/" + DEVICE_ID, tokenExpiry());

  while (!mqtt.connected()) {
    Serial.println("[HUB] Conectando al IoT Hub...");
    if (mqtt.connect(DEVICE_ID, user.c_str(), sas.c_str())) {
      Serial.println("[HUB] Conectado. (Connected en IoT Central)");
      mqtt.subscribe("$iothub/methods/POST/#");   // comandos (Direct Methods)
    } else {
      Serial.printf("[HUB] Fallo rc=%d. Reintentando...\n", mqtt.state());
      delay(3000);
    }
  }
}

// ---------------------------------------------------------------------
// Lectura de sensores y publicacion de telemetria
// ---------------------------------------------------------------------
void publishTelemetry() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  int raw = analogRead(POT_PIN);                 // 0..4095
  int lux = map(raw, 0, 4095, 0, 1000);          // illuminance 0..1000 lux

  if (isnan(t) || isnan(h)) {
    Serial.println("[DHT] lectura invalida, se omite envio.");
    return;
  }

  StaticJsonDocument<128> doc;
  doc["temperature"] = t;
  doc["humidity"]    = h;
  doc["illuminance"] = lux;

  char buf[128];
  size_t n = serializeJson(doc, buf);
  String topic = String("devices/") + DEVICE_ID + "/messages/events/";
  mqtt.publish(topic.c_str(), (const uint8_t *)buf, n, false);
  Serial.printf("[TX] %s\n", buf);
}

// ---------------------------------------------------------------------
void connectWiFi() {
  Serial.printf("[WiFi] Conectando a %s ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.printf("\n[WiFi] OK  IP=%s\n", WiFi.localIP().toString().c_str());
}

void syncTime() {
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.print("[TIME] Sincronizando NTP");
  while (time(nullptr) < 1700000000) { delay(300); Serial.print("."); }
  Serial.printf("\n[TIME] epoch=%ld\n", (long)time(nullptr));
}

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  dht.begin();

  // Decodificar la Primary Key (base64) a bytes para el HMAC del SAS
  if (mbedtls_base64_decode(decodedKey, sizeof(decodedKey), &decodedKeyLen,
        (const unsigned char *)DEVICE_KEY, strlen(DEVICE_KEY)) != 0) {
    Serial.println("[ERR] DEVICE_KEY no es base64 valido. Revisa la Primary Key.");
  }

  connectWiFi();
  syncTime();

  if (runDps() != "") {
    connectHub();
  } else {
    Serial.println("[FATAL] DPS fallo. Revisa ID_SCOPE / DEVICE_ID / DEVICE_KEY.");
  }
}

void loop() {
  if (!mqtt.connected() && assignedHub != "") connectHub();
  mqtt.loop();

  if (millis() - lastSend > SEND_INTERVAL_MS) {
    lastSend = millis();
    publishTelemetry();
  }
}
