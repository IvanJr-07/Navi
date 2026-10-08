"""
retroalimentacion.py — Lee los botones 👍 / 👎 que tocaste en Telegram.

Navi no está prendida todo el tiempo, así que no puede "escuchar" los
botones en el momento. En cambio, Telegram guarda lo que pasó en el chat
(hasta por 24 horas) y Navi lo recoge al despertar con getUpdates. Cada
reacción se guarda en la memoria, y el juez la usa como ejemplo para
calificar mejor la próxima vez.
"""
import re

from memoria import guardar_estado, leer_estado, registrar_feedback
from notificador import CHAT_ID, editar_botones, obtener_actualizaciones, responder_boton

CLAVE_OFFSET = "telegram_offset"  # Dónde se guarda hasta qué actualización ya leímos

# Formato válido del callback_data: "fb:+:<clave>" o "fb:-:<clave>",
# donde la clave son 12 caracteres hexadecimales (o "prueba").
PATRON_BOTON = re.compile(r"^fb:([+-]):([0-9a-f]{12}|prueba)$")


def _confirmar_en_botones(filas: list, clave: str, valor: int) -> list:
    """Reemplaza la fila de botones de esa publicación por una confirmación.

    Funciona igual para un aviso (una fila) y para el resumen (una fila por
    publicación): solo cambia la fila cuyo botón apunta a esa publicación.
    """
    objetivos = {f"fb:+:{clave}", f"fb:-:{clave}"}
    nuevas = []

    for fila in filas:
        datos = {boton.get("callback_data", "") for boton in fila}
        if datos & objetivos:  # Intersección de conjuntos: ¿esta fila es la de esa publicación?
            partes = fila[0].get("text", "").split(" ", 1)
            numero = f" · {partes[1]}" if len(partes) > 1 else ""
            texto = "✅ Te gustó" if valor > 0 else "✅ No te gustó"
            nuevas.append([{"text": texto + numero, "callback_data": "x"}])
        else:
            nuevas.append(fila)
    return nuevas


def procesar_retroalimentacion() -> int:
    """Lee los botones tocados desde la última ejecución. Regresa cuántas reacciones guardó."""
    offset = int(leer_estado(CLAVE_OFFSET, "0"))
    ok, actualizaciones = obtener_actualizaciones(offset)
    if not ok:
        return 0

    registradas = 0
    for actualizacion in actualizaciones:
        offset = max(offset, actualizacion["update_id"] + 1)

        boton = actualizacion.get("callback_query")
        if not boton:
            continue  # Por ejemplo, un mensaje que le escribiste al bot: se ignora

        mensaje = boton.get("message") or {}
        chat_id = mensaje.get("chat", {}).get("id")
        # Seguridad: solo se aceptan botones tocados en TU chat
        if str(chat_id) != str(CHAT_ID):
            continue

        coincidencia = PATRON_BOTON.match(boton.get("data", ""))
        if not coincidencia:
            # Botón de confirmación ("x") o de una versión anterior de Navi
            responder_boton(boton["id"])
            continue

        valor = 1 if coincidencia.group(1) == "+" else -1
        clave = coincidencia.group(2)

        if registrar_feedback(clave, valor):
            registradas += 1
        elif clave != "prueba":
            print(f"   ⚠️ La publicación {clave} ya no está en la memoria; se ignora.")

        responder_boton(boton["id"], "¡Anotado! 🧚")
        filas = (mensaje.get("reply_markup") or {}).get("inline_keyboard")
        if filas:
            editar_botones(chat_id, mensaje["message_id"], _confirmar_en_botones(filas, clave, valor))

    guardar_estado(CLAVE_OFFSET, str(offset))
    return registradas
