# Laboratorio 3 — MQTT hacia Azure IoT Central
### Protocolo visible, mediciones y comparación con el SDK

**Materia:** IoT + Cloud + Sistemas Distribuidos — Universidad Autónoma de Bucaramanga (UNAB)
**Escenario:** UNAB-Ambiental (continúa el Lab 1 y Lab 2, misma app `medidordeclima2026`)

Este laboratorio **abre la caja negra** del Lab 2: demuestra que por debajo el
transporte es **MQTT sobre TLS**, con un primer salto por **DPS** y luego el
**IoT Hub**. Se publican las **mismas 3 variables** (`temperature`, `humedad`,
`illuminance`) por dos caminos, y se compara con el ESP32 de Wokwi.

| Camino | Dónde | Qué se ve |
|---|---|---|
| **SDK** `azure-iot-device` | Python en la **VM de Azure** (Lab 2) | El SDK oculta topics y token. Es la línea base. |
| **MQTT explícito** `paho-mqtt` | **Misma VM de Azure** | Username, SAS, topic de telemetría y QoS **a la vista**. |
| **Arduino / ESP32** | Wokwi (Lab 2) | El firmware ya habla MQTT/TLS con Central (PubSubClient + SAS por mbedTLS). |

Los tres dispositivos reportan a la **misma aplicación** IoT Central sobre la
plantilla **Hobo MX-100 v3**.

---

## 1. Arquitectura del flujo MQTT

```
                         (1) DPS: primer salto
  dispositivo  ──MQTT/TLS 8883──►  global.azure-devices-provisioning.net
   (paho)      ◄── assignedHub ──   (registra y devuelve el IoT Hub)
      │
      │ (2) telemetría
      └────────MQTT/TLS 8883──────►  iotc-XXXX.azure-devices.net  (IoT Hub)
               topic:  devices/{deviceId}/messages/events/         │
               auth:   SAS (HMAC-SHA256)                           ▼
                                                       Azure IoT Central
                                                    (app UNAB-Ambiental,
                                                     Data explorer + Reglas)
```

- **Puerto:** 8883 (MQTT sobre TLS). También existe MQTT sobre WebSockets (443).
- **Autenticación:** token **SAS** por dispositivo (no usuario/clave planos).
- **QoS admitido por IoT Central:** **0 y 1**. **QoS 2 no** (ver §4).

## 2. Estructura del repositorio

```
laboratorio3/
├── README.md                     (este archivo)
├── mqtt/
│   ├── mqtt_explicito.py         ← cliente MQTT EXPLÍCITO (paho): DPS+SAS+publish+QoS
│   ├── mqtt-explicito.service    (unit systemd para dejarlo corriendo en la VM)
│   ├── requirements.txt          (paho-mqtt)
│   └── .env.example              (plantilla de variables; SIN claves)
├── sdk/
│   └── IoTCentralSender.py       (copia del dispositivo SDK del Lab 2, línea base)
├── mediciones/                   (logs y CSV reales de las corridas)
│   ├── log_qos1.txt  log_qos0.txt  log_qos2_debug.txt  log_corte.txt
│   └── qos0.csv  qos1.csv
├── evidencias/                   (capturas: logs renderizados + IoT Central)
└── Informe_Laboratorio3.pdf      (informe 1–2 páginas)
```

## 3. Cómo se genera el SAS (sin secretos en claro)

El token SAS se construye **a mano** (no lo da ningún SDK). Algoritmo:

```
recurso   = "{iothub}/devices/{deviceId}"          (URL-encoded)
expiry    = ahora + 3600 s                          (epoch)
stringToSign = urlencode(recurso) + "\n" + expiry
firma     = base64( HMAC-SHA256( base64decode(DEVICE_KEY), stringToSign ) )
SAS       = "SharedAccessSignature sr={urlencode(recurso)}"
            + "&sig={urlencode(firma)}&se={expiry}"
```

Para **DPS** el recurso es `{idScope}/registrations/{registrationId}` y el token
lleva además `&skn=registration`. La implementación está en
[`mqtt/mqtt_explicito.py`](mqtt/mqtt_explicito.py) → función `generar_sas()`.

> ⚠️ **Las claves NUNCA van en el repo.** Se leen de variables de entorno
> (`IOTC_DEVICE_KEY`, etc.). Copia `mqtt/.env.example` a `mqtt/.env` (ignorado por
> git) y complétalo. Si una Primary Key se expuso, **regenérala** en IoT Central.

## 4. Cómo ejecutar el cliente MQTT explícito

```bash
cd laboratorio3/mqtt
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # y completa IOTC_ID_SCOPE / IOTC_DEVICE_ID / IOTC_DEVICE_KEY
set -a; . ./.env; set +a

python mqtt_explicito.py                 # publica en bucle (QoS del .env)
IOTC_N=10 IOTC_QOS=0 python mqtt_explicito.py   # 10 mensajes en QoS 0
IOTC_N=10 IOTC_QOS=1 python mqtt_explicito.py   # 10 mensajes en QoS 1
IOTC_QOS=2 IOTC_DEBUG=1 python mqtt_explicito.py # QoS 2 + traza de paquetes
```

En la **VM de Azure** queda como servicio (`mqtt-explicito.service`), arranque
automático, junto al SDK del Lab 2 (`iotc-ambiental`). Ambos aparecen
**Connected** en la misma app.

## 5. QoS: qué acepta IoT Central (resultado medido)

| QoS publicado | Qué hace IoT Hub | Implicación |
|---|---|---|
| **0** | Acepta, *fire-and-forget*, sin PUBACK | Puede perder mensajes; menor latencia |
| **1** | Acepta y responde **PUBACK** (~140 ms) | *At-least-once*; reintenta y encola |
| **2** | **Responde PUBACK** (ack de QoS 1), **no** hace PUBREC/PUBREL/PUBCOMP | Se **degrada a QoS 1**; NO hay *exactly-once* |

Evidencia del QoS 2 en [`mediciones/log_qos2_debug.txt`](mediciones/log_qos2_debug.txt):
`Sending PUBLISH (q2) → Received PUBACK`.

## 6. Prueba de corte de red (10–20 s)

Bloqueando el puerto 8883 con `iptables` durante 16 s (ver
[`evidencias/02-corte-reconexion.png`](evidencias/02-corte-reconexion.png)):
el cliente detecta la caída (`rc=7`), **encola** los mensajes QoS 1, y al
restablecer **reconecta solo (~2 s)** y hace *flush* de todo lo pendiente
**sin pérdida** (los ACK atrasados muestran 15.4 s → 0.5 s).

## 7. Dispositivos en IoT Central (misma app `medidordeclima2026`)

| Dispositivo | Device ID | Camino |
|---|---|---|
| Python Aula 101 | `2c5qb72vl4u` | SDK (VM Azure, servicio `iotc-ambiental`) |
| MQTT-Explicito-Paho | `mqtt-explicito-lab3` | MQTT explícito (VM Azure, servicio `mqtt-explicito`) |
| ESP32-ve8okslkz9 | `ve8okslkz9` | Arduino/Wokwi (firmware MQTT explícito) |
| + 6 simulados | — | Flota del Lab 1/2 |

> El firmware del ESP32 **no cambió** respecto al Lab 2 (ya es MQTT explícito con
> PubSubClient + SAS por mbedTLS), por eso se reutiliza el proyecto de
> `laboratorio2/wokwi/`.
