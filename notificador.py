"""
notificador.py — Todo lo que Navi hace con Telegram:
enviar avisos y resúmenes (con botones 👍 / 👎), leer qué botones tocaste
y actualizar los mensajes.

Uso:
    python notificador.py chatid   -> muestra tu chat ID (solo la primera vez)
    python notificador.py          -> envía un mensaje de prueba con botones
"""
import html
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()  # Lee las variables del archivo .env

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_URL = f"https://api.telegram.org/bot{TOKEN}"

SALUDO = "🧚 <b>Hey! Listen!</b>"  # Encabezado de todos los avisos de Navi

MAX_INTENTOS = 3     # Reintentos si falla la conexión
TIMEOUT = 20         # Segundos máximos esperando a Telegram


def _ocultar_token(texto: str) -> str:
    """Reemplaza el token por *** en cualquier texto (por ejemplo, mensajes de error).

    La URL de la API de Telegram lleva el token dentro, así que un error
    de conexión lo imprimiría completo. Nunca debe aparecer en la terminal
    ni en registros (logs) que otras personas puedan ver.
    """
    if TOKEN:
        return texto.replace(TOKEN, "***")
    return texto


def _llamar_api(metodo: str, datos: dict, mostrar_errores: bool = True) -> tuple[bool, object]:
    """Llama a cualquier método de la API de Telegram, con reintentos.

    Regresa una tupla (ok, resultado). Se usa tupla y no solo el resultado
    porque algunos resultados válidos son "vacíos" (por ejemplo, una lista
    sin actualizaciones), y no queremos confundirlos con un error.
    """
    if not TOKEN:
        print("Falta TELEGRAM_TOKEN en el archivo .env")
        return False, None

    for intento in range(1, MAX_INTENTOS + 1):
        try:
            respuesta = requests.post(f"{API_URL}/{metodo}", json=datos, timeout=TIMEOUT)
        except requests.RequestException as error:
            # Errores de red: sin internet, timeout, Telegram caído...
            nombre = type(error).__name__
            if intento < MAX_INTENTOS:
                espera = 5 * intento  # 5 s, luego 10 s
                print(f"   ⚠️ No se pudo conectar con Telegram ({nombre}); reintento en {espera} s...")
                time.sleep(espera)
                continue
            print(f"   ❌ Telegram no respondió tras {MAX_INTENTOS} intentos ({nombre}).")
            print(f"      Detalle: {_ocultar_token(str(error))[:200]}")
            return False, None

        try:
            cuerpo = respuesta.json()
        except ValueError:
            cuerpo = {}

        if respuesta.ok and cuerpo.get("ok"):
            return True, cuerpo.get("result")

        # Telegram respondió, pero con error (token inválido, chat incorrecto...)
        if mostrar_errores:
            print(f"   ❌ Error de Telegram en {metodo} ({respuesta.status_code}): "
                  f"{_ocultar_token(respuesta.text)[:300]}")
        return False, None

    return False, None


# ---------------------------------------------------------------- Enviar mensajes

def botones_feedback(clave: str, numero: int | None = None) -> list[dict]:
    """Una fila con los botones 👍 y 👎 de una publicación.

    Cuando tocas un botón, Telegram le devuelve al bot el texto de
    "callback_data" (máximo 64 caracteres), por ejemplo "fb:+:3f9a1c2b7d4e":
    fb = feedback, + = te gustó, y la clave de la publicación en la memoria.
    """
    etiqueta = f" {numero}" if numero is not None else ""
    return [
        {"text": f"👍{etiqueta}", "callback_data": f"fb:+:{clave}"},
        {"text": f"👎{etiqueta}", "callback_data": f"fb:-:{clave}"},
    ]


def enviar_mensaje(texto: str, filas_botones: list | None = None, sin_vista_previa: bool = False) -> bool:
    """Envía un mensaje a tu chat. Regresa True si se envió bien, False si no."""
    if not CHAT_ID:
        print("Falta TELEGRAM_CHAT_ID en el archivo .env")
        return False

    datos = {
        "chat_id": CHAT_ID,
        "text": texto,
        "parse_mode": "HTML",  # Permite <b>negritas</b> y <a href="...">enlaces</a>
    }
    if filas_botones:
        datos["reply_markup"] = {"inline_keyboard": filas_botones}
    if sin_vista_previa:
        datos["link_preview_options"] = {"is_disabled": True}

    ok, _ = _llamar_api("sendMessage", datos)
    return ok


def enviar_alerta(titulo: str, detalle: str, enlace: str = "", clave: str | None = None) -> bool:
    """Envía un aviso con el formato de Navi: saludo, título, detalle, enlace y botones.

    html.escape() convierte símbolos como <, > y & en texto seguro;
    si no, un título como "Mario & Luigi" rompería el formato HTML de Telegram.
    """
    texto = f"{SALUDO}\n\n<b>{html.escape(titulo)}</b>\n{html.escape(detalle)}"
    if enlace:
        texto += f'\n\n<a href="{html.escape(enlace, quote=True)}">Ver más</a>'

    botones = [botones_feedback(clave)] if clave else None
    return enviar_mensaje(texto, botones)


def enviar_resumen(publicaciones, titulo: str = "Resumen del día") -> bool:
    """Envía UN mensaje con varias publicaciones "de frontera", numeradas, con botones por cada una."""
    lineas = [f"🧚 <b>{html.escape(titulo)}</b>", "Cosas que tal vez te interesen:", ""]
    filas = []

    for numero, p in enumerate(publicaciones, start=1):
        titulo = html.escape(p["resumen_es"] or p["titulo"])
        enlace = html.escape(p["enlace"], quote=True)
        fuente = html.escape(p["fuente"])
        lineas.append(f'{numero}. <a href="{enlace}">{titulo}</a> <i>({p["puntuacion"]}/10 · {fuente})</i>')
        filas.append(botones_feedback(p["clave"], numero))

    lineas += ["", "Toca 👍 o 👎 para enseñarme qué te gusta."]
    # Sin vista previa: con varios enlaces, Telegram solo mostraría el primero y confunde
    return enviar_mensaje("\n".join(lineas), filas, sin_vista_previa=True)


# ---------------------------------------------------------------- Leer y responder botones

def obtener_actualizaciones(offset: int) -> tuple[bool, list]:
    """Pide a Telegram lo que pasó en el chat desde la última vez (botones tocados, mensajes...).

    "offset" le dice a Telegram desde qué actualización empezar; las anteriores
    se consideran confirmadas y ya no las vuelve a mandar.
    Telegram guarda las actualizaciones pendientes hasta por 24 horas.
    """
    ok, resultado = _llamar_api("getUpdates", {"offset": offset, "timeout": 0})
    return ok, (resultado or [])


def responder_boton(id_callback: str, texto: str = "") -> None:
    """Le confirma a Telegram que se recibió el toque del botón.

    Si pasó mucho tiempo desde que lo tocaste, Telegram ya no lo acepta;
    por eso se ignoran los errores aquí.
    """
    _llamar_api("answerCallbackQuery", {"callback_query_id": id_callback, "text": texto},
                mostrar_errores=False)


def editar_botones(chat_id, id_mensaje: int, filas_botones: list) -> None:
    """Reemplaza los botones de un mensaje ya enviado."""
    _llamar_api(
        "editMessageReplyMarkup",
        {"chat_id": chat_id, "message_id": id_mensaje,
         "reply_markup": {"inline_keyboard": filas_botones}},
        mostrar_errores=False,  # Ej. "message is not modified" no es un problema real
    )


# ---------------------------------------------------------------- Utilidad: chat ID

def obtener_chat_id() -> None:
    """Muestra el chat ID de quien le escribió al bot. Solo se usa una vez."""
    ok, actualizaciones = _llamar_api("getUpdates", {})
    if not ok:
        return

    encontrados = False
    for update in actualizaciones:
        chat = update.get("message", {}).get("chat")
        if chat:
            print(f"Chat ID: {chat['id']}  ({chat.get('first_name', '')})")
            encontrados = True

    if not encontrados:
        print("No hay mensajes. Escríbele cualquier cosa a Navi en Telegram y vuelve a intentar.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chatid":
        obtener_chat_id()
    else:
        # Clave "prueba": no corresponde a ninguna publicación real
        ok = enviar_alerta(
            "¡Navi está despierta!",
            "Ya puedo mandarte mensajes, Ivan. Toca 👍 o 👎 para probar los botones; "
            "los registraré la próxima vez que me ejecute.",
            clave="prueba",
        )
        print("Mensaje enviado ✅" if ok else "No se pudo enviar ❌")
