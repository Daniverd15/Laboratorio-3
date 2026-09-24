#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Laboratorio 3 - UNAB-Ambiental
Cliente MQTT EXPLICITO hacia Azure IoT Central (sin azure-iot-device).

Hace visible TODO el protocolo que el SDK del Lab 2 ocultaba:
  1) Genera a mano el token SAS (HMAC-SHA256 de la clave del dispositivo).
  2) Aprovisiona el dispositivo por DPS usando MQTT/TLS (el "primer salto").
  3) Con el IoT Hub que devuelve DPS, se conecta por MQTT/TLS 8883.
  4) Publica las 3 variables (temperature, humidity, illuminance) al topic
     de telemetria de IoT Hub:  devices/{id}/messages/events/
  5) Se suscribe a metodos directos (C2D) para ver el comando setAlertLed.

Todo con paho-mqtt: username, SAS, topics y QoS quedan a la vista.

Variables de entorno (ver .env.example):
  IOTC_ID_SCOPE      ID Scope de la app  (ej. 0ne00FDCA1E)
  IOTC_DEVICE_ID     Device ID           (ej. mqtt-explicito-lab3)
  IOTC_DEVICE_KEY    Primary key (base64) del dispositivo
  IOTC_SEND_INTERVAL Segundos entre publicaciones (default 5)
  IOTC_QOS           QoS a usar: 0, 1 o 2 (default 1).  IoT Central admite 0/1, NO 2.
  IOTC_N             Numero de mensajes y termina (0 = infinito, default 0)
  IOTC_CSV           Ruta de un CSV donde volcar las mediciones (opcional)
"""

import os, ssl, sys, time, json, hmac, hashlib, base64, urllib.parse, random, threading

import paho.mqtt.client as mqtt

# ----------------------------- Configuracion -----------------------------
ID_SCOPE  = os.environ["IOTC_ID_SCOPE"]
DEVICE_ID = os.environ["IOTC_DEVICE_ID"]
DEVICE_KEY = os.environ["IOTC_DEVICE_KEY"]
INTERVAL  = int(os.environ.get("IOTC_SEND_INTERVAL", "5"))
QOS       = int(os.environ.get("IOTC_QOS", "1"))
N_MSGS    = int(os.environ.get("IOTC_N", "0"))          # 0 = infinito
CSV_PATH  = os.environ.get("IOTC_CSV", "")

DPS_ENDPOINT = "global.azure-devices-provisioning.net"
DPS_API      = "2019-03-31"
HUB_API      = "2021-04-12"

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print("%s %s" % (ts, msg), flush=True)

# --------------------------- Generacion del SAS ---------------------------
def generar_sas(resource_uri, key_b64, expiry_seconds=3600, policy_name=None):
    """Construye un token SAS:  SharedAccessSignature sr=..&sig=..&se=..[&skn=..]
    La firma es HMAC-SHA256( base64decode(clave), "<uri>\n<expiry>" )."""
    encoded_uri = urllib.parse.quote(resource_uri, safe="")
    expiry = int(time.time()) + expiry_seconds
    to_sign = ("%s\n%d" % (encoded_uri, expiry)).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(base64.b64decode(key_b64), to_sign, hashlib.sha256).digest()
    ).decode("utf-8")
    encoded_sig = urllib.parse.quote(signature, safe="")
    token = "SharedAccessSignature sr=%s&sig=%s&se=%d" % (encoded_uri, encoded_sig, expiry)
    if policy_name:
        token += "&skn=%s" % policy_name
    return token, expiry

def nuevo_cliente(client_id):
    """Crea un cliente paho compatible con paho-mqtt v1.x y v2.x."""
    try:  # paho-mqtt >= 2.0
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                        client_id=client_id, protocol=mqtt.MQTTv311)
    except (AttributeError, TypeError):  # paho-mqtt 1.x
        c = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)
    return c

def tls_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_default_certs()          # CA del sistema (DigiCert Global Root G2)
    return ctx

# ------------------------- Etapa 1: DPS por MQTT ---------------------------
def provisionar_por_mqtt():
    reg_id = DEVICE_ID
    resource_uri = "%s/registrations/%s" % (ID_SCOPE, reg_id)
    sas, exp = generar_sas(resource_uri, DEVICE_KEY, policy_name="registration")
    username = "%s/registrations/%s/api-version=%s" % (ID_SCOPE, reg_id, DPS_API)

    log("[SAS] Token de DPS generado (sr=%s, expira en 3600s)" % resource_uri)
    log("[DPS] Conectando por MQTT/TLS a %s:8883 ..." % DPS_ENDPOINT)

    resultado = {"hub": None, "err": None, "op": None}
    listo = threading.Event()

    c = nuevo_cliente(reg_id)
    c.username_pw_set(username=username, password=sas)
    c.tls_set_context(tls_context())

    def on_connect(cl, u, flags, rc):
        log("[DPS] CONNACK rc=%s -> suscribiendo a $dps/registrations/res/#" % rc)
        cl.subscribe("$dps/registrations/res/#", qos=1)
        body = json.dumps({"registrationId": reg_id})
        cl.publish("$dps/registrations/PUT/iotdps-register/?$rid=1", body, qos=1)
        log("[DPS] PUBLISH registro -> $dps/registrations/PUT/iotdps-register/ (%s)" % body)

    def on_message(cl, u, msg):
        payload = msg.payload.decode("utf-8", "replace")
        if "/res/202" in msg.topic:                # aceptado, hay que consultar estado
            op = json.loads(payload).get("operationId")
            resultado["op"] = op
            log("[DPS] 202 Assigning... operationId recibido, consultando estado")
            time.sleep(2)
            cl.publish("$dps/registrations/GET/iotdps-get-operationstatus/?$rid=2&operationId=%s" % op,
                       "{}", qos=1)
        elif "/res/200" in msg.topic:              # asignacion final
            st = json.loads(payload).get("registrationState", {})
            resultado["hub"] = st.get("assignedHub")
            log("[DPS] 200 assigned -> hub=%s  status=%s" % (resultado["hub"], st.get("status")))
            listo.set()
        else:
            code = msg.topic.split("/res/")[-1].split("/")[0]
            resultado["err"] = "DPS respondio %s: %s" % (code, payload[:200])
            log("[DPS] respuesta inesperada (%s): %s" % (code, payload[:200]))
            listo.set()

    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(DPS_ENDPOINT, 8883, keepalive=60)
    c.loop_start()
    if not listo.wait(timeout=40):
        resultado["err"] = "timeout esperando a DPS"
    c.loop_stop(); c.disconnect()
    if not resultado["hub"]:
        raise RuntimeError("DPS no asigno hub: %s" % resultado.get("err"))
    return resultado["hub"]

# --------------- Etapa 2/3: conexion al Hub y publicaciones ----------------
def lectura_sensor():
    """Mismas 3 variables del Lab 2, con los nombres EXACTOS de la plantilla
    Hobo MX-100 v3 (temperature / humedad / illuminance) para que mapeen y no
    caigan en _unmodeleddata."""
    return {
        "temperature": round(random.uniform(20.0, 32.0), 1),
        "humedad":     round(random.uniform(40.0, 70.0), 1),
        "illuminance": random.randint(150, 750),
    }

def main():
    hub = provisionar_por_mqtt()

    resource_uri = "%s/devices/%s" % (hub, DEVICE_ID)
    sas, exp = generar_sas(resource_uri, DEVICE_KEY)
    username = "%s/%s/?api-version=%s" % (hub, DEVICE_ID, HUB_API)
    topic_tx = "devices/%s/messages/events/" % DEVICE_ID
    topic_methods = "$iothub/methods/POST/#"

    log("[SAS] Token de IoT Hub generado (sr=%s)" % resource_uri)
    log("[HUB] Conectando por MQTT/TLS a %s:8883  (QoS=%d)" % (hub, QOS))

    metricas = []       # (n, bytes, latencia_ms) por PUBACK
    pend = {}           # mid -> (n, t_envio, bytes)
    estado = {"conn": False, "n_ack": 0}

    c = nuevo_cliente(DEVICE_ID)
    c.username_pw_set(username=username, password=sas)
    c.tls_set_context(tls_context())
    c.reconnect_delay_set(min_delay=1, max_delay=16)   # backoff de reconexion

    def on_connect(cl, u, flags, rc):
        estado["conn"] = (rc == 0)
        log("[HUB] CONNACK rc=%s (%s)" % (rc, "conectado" if rc == 0 else "RECHAZADO"))
        if rc == 0:
            cl.subscribe(topic_methods, qos=0)
            log("[HUB] Suscrito a metodos directos: %s" % topic_methods)

    def on_disconnect(cl, u, rc):
        estado["conn"] = False
        log("[HUB] DESCONECTADO (rc=%s). paho intentara reconectar con backoff..." % rc)

    def on_publish(cl, u, mid):
        info = pend.pop(mid, None)
        if not info:
            return
        n, t0, nb = info
        if QOS == 0:
            # QoS 0 no lleva PUBACK: on_publish solo confirma el envio a la red.
            metricas.append((n, nb, None))
            log("[SENT #%d] enviado a la red (qos=0, sin PUBACK)" % n)
        else:
            lat = (time.time() - t0) * 1000.0
            estado["n_ack"] += 1
            metricas.append((n, nb, lat))
            log("[ACK #%d] PUBACK en %.0f ms (qos=%d)" % (n, lat, QOS))

    def on_message(cl, u, msg):
        # Comando setAlertLed llega como metodo directo:
        #   $iothub/methods/POST/setAlertLed/?$rid=NN
        try:
            rid = msg.topic.split("$rid=")[1]
            metodo = msg.topic.split("/POST/")[1].split("/")[0]
        except Exception:
            return
        payload = msg.payload.decode("utf-8", "replace")
        log("[C2D] Metodo directo '%s' rid=%s payload=%s" % (metodo, rid, payload))
        estado_led = "ON"
        try:
            estado_led = "ON" if json.loads(payload) else "OFF"
        except Exception:
            pass
        log("[LED] setAlertLed -> %s" % estado_led)
        cl.publish("$iothub/methods/res/200/?$rid=%s" % rid,
                   json.dumps({"result": "LED %s" % estado_led}), qos=0)

    c.on_connect = on_connect
    c.on_disconnect = on_disconnect
    c.on_publish = on_publish
    c.on_message = on_message
    if os.environ.get("IOTC_DEBUG"):
        # Muestra los paquetes MQTT crudos (CONNECT/PUBLISH/PUBACK/PUBREC/PUBREL/PUBCOMP)
        c.on_log = lambda cl, u, level, buf: log("[MQTT] %s" % buf)
    c.connect(hub, 8883, keepalive=120)
    c.loop_start()

    # esperar CONNACK
    for _ in range(50):
        if estado["conn"]:
            break
        time.sleep(0.1)

    n = 0
    try:
        while True:
            n += 1
            datos = lectura_sensor()
            payload = json.dumps(datos, separators=(",", ":"))
            nb = len(payload.encode("utf-8"))
            info = c.publish(topic_tx, payload, qos=QOS)
            pend[info.mid] = (n, time.time(), nb)
            log("[TX #%d] topic=%s qos=%d bytes=%d payload=%s"
                % (n, topic_tx, QOS, nb, payload))
            if N_MSGS and n >= N_MSGS:
                time.sleep(2)   # dar tiempo al ultimo PUBACK
                break
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        log("[FIN] Interrumpido por el usuario.")
    finally:
        c.loop_stop(); c.disconnect()
        resumen(metricas)
        if CSV_PATH:
            volcar_csv(metricas, CSV_PATH)

def resumen(metricas):
    if not metricas:
        return
    tamanos = [m[1] for m in metricas]
    lats = [m[2] for m in metricas if m[2] is not None]
    log("---- RESUMEN ----")
    log("[MED] mensajes=%d  payload_bytes: min=%d max=%d prom=%.1f  QoS=%d"
        % (len(metricas), min(tamanos), max(tamanos), sum(tamanos)/len(tamanos), QOS))
    if lats:
        log("[MED] latencia PUBACK ms: min=%.0f max=%.0f prom=%.0f (n=%d)"
            % (min(lats), max(lats), sum(lats)/len(lats), len(lats)))
    else:
        log("[MED] QoS 0: sin PUBACK, no hay latencia de confirmacion que medir.")

def volcar_csv(metricas, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("n,payload_bytes,latencia_puback_ms,qos\n")
        for (n, nb, lat) in metricas:
            f.write("%d,%d,%s,%d\n" % (n, nb, ("" if lat is None else "%.0f" % lat), QOS))
    log("[MED] CSV escrito en %s" % path)

if __name__ == "__main__":
    main()
