"""
IoTCentralSender.py  -  UNAB-Ambiental  (Laboratorio 2)
=======================================================
Dispositivo Python que corre DENTRO de una maquina virtual y publica
telemetria ambiental a Azure IoT Central usando DPS + azure-iot-device.

Escenario (heredado del Laboratorio 1 - plantilla "Hobo MX-100"):
    Telemetrias : temperature (C), humidity (% RH), illuminance (lux)
    Propiedad writable : samplingIntervalSec  (intervalo de muestreo)
    Comando           : setAlertLed           (enciende/apaga la "alerta")
    Propiedad reported: estadoAlerta           (normal / alerta)

REGLAS DE SEGURIDAD
    - NUNCA escribas credenciales en este archivo.
    - Las credenciales se leen de variables de entorno:
        IOTC_ID_SCOPE    -> ID Scope de la app (ej: 0ne00XXXXXX)
        IOTC_DEVICE_ID   -> Device ID registrado en IoT Central
        IOTC_DEVICE_KEY  -> Primary key (SAS) del dispositivo
      Opcional:
        IOTC_SEND_INTERVAL -> segundos entre envios (default: 5)

Requisitos: Python 3.9+  y  azure-iot-device  (ver requirements.txt)
Ejecucion:  python IoTCentralSender.py
"""

import os
import json
import asyncio
import random

from azure.iot.device import Message, MethodResponse
from azure.iot.device.aio import IoTHubDeviceClient, ProvisioningDeviceClient

# ---------------------------------------------------------------------------
# 1) Configuracion desde variables de entorno (sin secretos en el codigo)
# ---------------------------------------------------------------------------
PROVISIONING_HOST = "global.azure-devices-provisioning.net"

ID_SCOPE   = os.getenv("IOTC_ID_SCOPE")
DEVICE_ID  = os.getenv("IOTC_DEVICE_ID")
DEVICE_KEY = os.getenv("IOTC_DEVICE_KEY")

# Estado dinamico. samplingIntervalSec es una propiedad writable del Lab 1:
# cuando el operador la cambia desde IoT Central, este valor se actualiza.
sampling_interval = int(os.getenv("IOTC_SEND_INTERVAL", "5"))
alert_led_on = False


def _require_env():
    faltan = [n for n, v in (
        ("IOTC_ID_SCOPE", ID_SCOPE),
        ("IOTC_DEVICE_ID", DEVICE_ID),
        ("IOTC_DEVICE_KEY", DEVICE_KEY),
    ) if not v]
    if faltan:
        raise SystemExit(
            "ERROR: faltan variables de entorno -> " + ", ".join(faltan) +
            "\nDefinelas antes de ejecutar (ver README)."
        )


# ---------------------------------------------------------------------------
# 2) Aprovisionamiento con DPS (Device Provisioning Service)
# ---------------------------------------------------------------------------
async def provision():
    prov = ProvisioningDeviceClient.create_from_symmetric_key(
        provisioning_host=PROVISIONING_HOST,
        registration_id=DEVICE_ID,
        id_scope=ID_SCOPE,
        symmetric_key=DEVICE_KEY,
    )
    result = await prov.register()
    if result.status != "assigned":
        raise SystemExit(f"DPS no asigno el dispositivo (status={result.status}).")
    hub = result.registration_state.assigned_hub
    print(f"[DPS] Dispositivo asignado al hub: {hub}")
    return hub


# ---------------------------------------------------------------------------
# 3) Generacion de datos ambientales sinteticos y coherentes
# ---------------------------------------------------------------------------
def synth_environment():
    """Valores realistas para un aula/laboratorio UNAB-Ambiental."""
    return {
        "temperature": round(random.uniform(20.0, 32.0), 1),  # C
        "humedad":     round(random.uniform(40.0, 70.0), 1),  # % RH (nombre exacto de la plantilla)
        "illuminance": random.randint(150, 750),              # lux
    }


# ---------------------------------------------------------------------------
# 4) Programa principal
# ---------------------------------------------------------------------------
async def main():
    _require_env()
    hub = await provision()

    device = IoTHubDeviceClient.create_from_symmetric_key(
        symmetric_key=DEVICE_KEY,
        hostname=hub,
        device_id=DEVICE_ID,
    )
    await device.connect()
    print(f"[IoTHub] Conectado como '{DEVICE_ID}'. Ctrl+C para detener.")

    # Propiedades reportadas iniciales (aparecen en la vista del dispositivo)
    await device.patch_twin_reported_properties({
        "samplingIntervalSec": sampling_interval,
        "estadoAlerta": "normal",
    })

    # --- Comando setAlertLed (Direct Method) ------------------------------
    async def on_method_request(request):
        global alert_led_on
        print(f"[CMD] {request.name}  payload={request.payload}")
        if request.name == "setAlertLed":
            p = request.payload
            if isinstance(p, dict):
                alert_led_on = bool(p.get("state", not alert_led_on))
            elif isinstance(p, bool):
                alert_led_on = p
            else:
                alert_led_on = not alert_led_on
            estado = "alerta" if alert_led_on else "normal"
            print(f"[LED] setAlertLed -> {'ON' if alert_led_on else 'OFF'}")
            await device.patch_twin_reported_properties({"estadoAlerta": estado})
            resp = MethodResponse.create_from_method_request(
                request, 200, {"result": f"LED {'ON' if alert_led_on else 'OFF'}"})
        else:
            resp = MethodResponse.create_from_method_request(
                request, 404, {"result": f"comando desconocido: {request.name}"})
        await device.send_method_response(resp)

    device.on_method_request_received = on_method_request

    # --- Propiedad writable samplingIntervalSec (desired twin) ------------
    async def on_twin_patch(patch):
        global sampling_interval
        print(f"[TWIN] desired patch: {patch}")
        if "samplingIntervalSec" in patch:
            try:
                sampling_interval = max(1, int(patch["samplingIntervalSec"]))
            except (TypeError, ValueError):
                return
            version = patch.get("$version", 1)
            # Acuse que IoT Central marca como 'synced' (formato ac/ad/av)
            await device.patch_twin_reported_properties({
                "samplingIntervalSec": {
                    "value": sampling_interval,
                    "ac": 200, "ad": "aplicado", "av": version,
                }
            })
            print(f"[TWIN] samplingIntervalSec -> {sampling_interval}s")

    device.on_twin_desired_properties_patch_received = on_twin_patch

    # --- Bucle de telemetria ----------------------------------------------
    try:
        while True:
            data = synth_environment()
            msg = Message(json.dumps(data))
            msg.content_encoding = "utf-8"
            msg.content_type = "application/json"
            await device.send_message(msg)
            print(f"[TX] {data}  (cada {sampling_interval}s)")
            await asyncio.sleep(sampling_interval)
    except KeyboardInterrupt:
        print("\nDetenido por el usuario.")
    finally:
        await device.shutdown()
        print("[IoTHub] Desconectado.")


if __name__ == "__main__":
    asyncio.run(main())
