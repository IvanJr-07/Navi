"""
guardia.py — Decide si a Navi ya le toca ejecutarse.

GitHub intenta correr a Navi cada 10 minutos, pero muchas de esas
ejecuciones las retrasa o descarta. Para que Navi trabaje más o menos
una vez por hora, este guardia revisa en la memoria cuándo fue la última
ejecución completa: si fue hace menos de MINUTOS_ENTRE_EJECUCIONES, le
dice al workflow que se salte el resto de los pasos.

Solo usa la librería estándar de Python (sqlite3), así que corre antes de
instalar nada, y la ejecución que se salta termina en segundos.

Uso (lo llama el workflow):
    python3 guardia.py
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ARCHIVO_DB = "navi.db"
MINUTOS_ENTRE_EJECUCIONES = 50


def ultima_ejecucion() -> datetime | None:
    """Lee de la memoria la fecha de la última ejecución completa, si existe."""
    if not os.path.exists(ARCHIVO_DB):
        return None
    try:
        with sqlite3.connect(ARCHIVO_DB) as conexion:
            fila = conexion.execute(
                "SELECT valor FROM estado WHERE clave = 'ultima_ejecucion'"
            ).fetchone()
    except sqlite3.Error:
        return None  # Tabla inexistente o base de datos dañada: mejor ejecutar
    return datetime.fromisoformat(fila[0]) if fila else None


def decidir() -> bool:
    """Regresa True si Navi debe saltarse esta ejecución."""
    # Las ejecuciones manuales (botón "Run workflow") siempre corren
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        print("▶️ Ejecución manual: Navi corre siempre.")
        return False

    ultima = ultima_ejecucion()
    if ultima is None:
        print("▶️ No hay registro de ejecuciones anteriores: Navi corre.")
        return False

    transcurrido = datetime.now(timezone.utc) - ultima
    minutos = int(transcurrido.total_seconds() // 60)
    if transcurrido < timedelta(minutes=MINUTOS_ENTRE_EJECUCIONES):
        print(f"⏭️ Navi corrió hace {minutos} min; se salta esta vez.")
        return True

    print(f"▶️ Navi corrió hace {minutos} min: le toca de nuevo.")
    return False


if __name__ == "__main__":
    saltar = decidir()
    # GITHUB_OUTPUT es un archivo donde un paso deja valores para los siguientes pasos
    salida = os.getenv("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a", encoding="utf-8") as archivo:
            archivo.write(f"saltar={'true' if saltar else 'false'}\n")
    sys.exit(0)
