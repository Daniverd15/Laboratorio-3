# Pasos 9–10 (Laboratorio 2) — ESP32 en **VS Code** (PlatformIO + Wokwi)

> El profesor recomendó VS Code porque la web de Wokwi está saturada. El propio
> enunciado del Lab 2 lo permite: *"Wokwi (wokwi.com **o Wokwi for VS Code**)"*.
> Aquí compilamos el firmware con **PlatformIO** y lo simulamos con la extensión
> **Wokwi for VS Code** (mismo ESP32 + DHT22 + potenciómetro + LED, sin hardware
> físico).
>
> Nota: es **Visual Studio *Code*** (VS Code), no "Visual Studio" a secas. Para
> ESP32/Arduino se usa VS Code.

## 0. Archivos de este proyecto (carpeta `wokwi/`)
```
wokwi/
├── platformio.ini      ← configuración de compilación (board esp32dev + librerías)
├── wokwi.toml          ← apunta al firmware compilado para el simulador
├── diagram.json        ← ESP32 + DHT22 + potenciómetro + LED
├── src/
│   └── main.cpp        ← firmware (mismo código que sketch.ino)
├── sketch.ino          ← copia para la entrega (versión Arduino/Wokwi web)
└── libraries.txt       ← lista de librerías (entrega)
```

## 9.1 — Instalar extensiones en VS Code
1. Abre **VS Code**.
2. Ve a **Extensions** (Ctrl+Shift+X) e instala:
   - **PlatformIO IDE** (autor: PlatformIO). Espera a que termine de instalar el core (puede tardar varios minutos la primera vez).
   - **Wokwi Simulator** (autor: Wokwi).
3. Reinicia VS Code si lo pide.

## 9.2 — Abrir el proyecto
1. **File → Open Folder…** y selecciona la carpeta `laboratorio2/wokwi`.
2. PlatformIO detecta `platformio.ini`. Espera a que instale las librerías
   (PubSubClient, DHT, Adafruit Unified Sensor, ArduinoJson).

## 9.3 — Poner tus credenciales
Abre `src/main.cpp` y rellena, SOLO para probar (no lo subas con la clave real):
```cpp
#define ID_SCOPE    "0ne00FDCA1E"          // tu ID Scope
#define DEVICE_ID   "esp32-aula-201"       // Device ID del ESP32 (real, Simular=No)
#define DEVICE_KEY  "CLAVE_PRINCIPAL_DEL_ESP32"
```
> Antes registra el ESP32 en IoT Central: **Devices → + Nuevo → plantilla
> `Hobo MX-100 v3` → Simular = No → Crear → Conectar** y copia su Primary Key.

## 9.4 — Compilar el firmware (PlatformIO)
1. Barra inferior de PlatformIO → icono **✓ (Build)**, o **Ctrl+Alt+B**.
2. *Qué debería aparecer:* `SUCCESS` y se genera
   `.pio/build/esp32dev/firmware.bin` y `firmware.elf`.
   - ❌ Si falla, revisa que las librerías se instalaron (`platformio.ini`).

## 9.5 — Iniciar la simulación (Wokwi for VS Code)
1. La extensión Wokwi pide una **licencia gratuita** la primera vez:
   - Abre la paleta de comandos (**Ctrl+Shift+P**) → **"Wokwi: Request a License"**
     → se abre el navegador → inicia sesión → **Wokwi: Manage License** confirma.
2. Con `wokwi.toml` y `diagram.json` presentes, abre la paleta →
   **"Wokwi: Start Simulator"**.
3. Se abre el panel del simulador con el ESP32, el DHT22, el potenciómetro y el LED.

## 9.6 — Comprobar la conexión
En el panel del simulador verás el **monitor serie**:
```
[WiFi] OK  IP=10.x.x.x
[TIME] epoch=...
[DPS] assignedHub = iotc-xxxx.azure-devices.net
[HUB] Conectado. (Connected en IoT Central)
[TX] {"temperature":25.3,"humidity":60,"illuminance":512}
```
En IoT Central, el dispositivo `esp32-aula-201` pasa a **Connected** y llegan las
3 variables. 📷 **Capturas 09, 10, 11**.

- Puedes mover el **potenciómetro** (clic sobre él) para cambiar `illuminance`.
- El **DHT22** deja ajustar temperatura/humedad al pasar el mouse por encima.

## 10 — Probar el comando `setAlertLed`
1. IoT Central → `esp32-aula-201` → **Commands** → `setAlertLed`
   (si añadiste el parámetro `state`, ponlo en **true**) → **Run**.
2. En el simulador de VS Code el **LED se enciende** y el serie muestra
   `[CMD] setAlertLed -> ON`. Envía `false` para apagarlo. 📷 **Captura 12**.

## Problemas frecuentes (VS Code)
| Síntoma | Solución |
|---|---|
| PlatformIO no compila | Espera a que termine de instalar el core; reintenta Build |
| "firmware.bin not found" al simular | Primero **Build**; el `.bin` debe existir en `.pio/build/esp32dev/` |
| Wokwi pide licencia | Ejecuta **Wokwi: Request a License** (es gratis) |
| No hay Internet en el simulador | El SSID debe ser `Wokwi-GUEST` sin contraseña |
| DHT lee `nan` | DATA del DHT22 en GPIO15; VCC a 3V3 |
| DPS falla / Unauthorized | Revisa ID_SCOPE / DEVICE_ID / DEVICE_KEY (clave del **dispositivo**, no de grupo) |
