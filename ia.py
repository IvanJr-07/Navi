"""
ia.py — Conexión con el modelo de IA que usa Navi.

Este archivo es una "abstracción": el resto del bot solo llama a
consultar_ia() o consultar_ia_json() y no sabe ni le importa qué
empresa está detrás. Para cambiar de proveedor solo se cambia
IA_PROVEEDOR en el .env (gemini o claude).

GEMINI_MODELO acepta varios modelos separados por comas. Si el primero
está saturado (503), limitado (429) o ya no existe (404), Navi prueba
con el siguiente. Ejemplo en el .env:
    GEMINI_MODELO=gemini-3.8-flash,otro-modelo-de-respaldo
"""
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

PROVEEDOR = os.getenv("IA_PROVEEDOR", "gemini").lower()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODELOS = [
    modelo.strip()
    for modelo in os.getenv("GEMINI_MODELO", "gemini-3.8-flash").split(",")
    if modelo.strip()
]

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODELO = os.getenv("CLAUDE_MODELO", "claude-haiku-4-5-20251001")

MAX_INTENTOS = 3                      # Reintentos por modelo si está saturado
CODIGOS_REINTENTABLES = (429, 500, 503)
CODIGOS_CAMBIAR_MODELO = (404, 429, 500, 503)


class ErrorIA(Exception):
    """Error propio para cualquier problema al hablar con la IA."""

    def __init__(self, mensaje: str, codigo: int | None = None):
        super().__init__(mensaje)
        self.codigo = codigo  # Código HTTP del error, si lo hay


def _post_con_reintentos(url: str, headers: dict, cuerpo: dict) -> dict:
    """Hace la petición a la API; si está saturada, espera y reintenta (backoff)."""
    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = requests.post(url, headers=headers, json=cuerpo, timeout=90)

        if respuesta.status_code in CODIGOS_REINTENTABLES and intento < MAX_INTENTOS:
            espera = 15 * intento  # 15 s, 30 s...
            print(f"   ⏳ La IA respondió {respuesta.status_code}; reintento en {espera} s...")
            time.sleep(espera)
            continue

        if not respuesta.ok:
            raise ErrorIA(
                f"Error {respuesta.status_code}: {respuesta.text[:300]}",
                codigo=respuesta.status_code,
            )
        return respuesta.json()

    raise ErrorIA("Se agotaron los reintentos con la IA")


def _consultar_un_modelo_gemini(modelo: str, instrucciones: str, mensaje: str) -> str:
    """Envía el mensaje a UN modelo de Gemini y regresa el texto de la respuesta."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
    headers = {"x-goog-api-key": GEMINI_KEY, "Content-Type": "application/json"}
    cuerpo = {
        "systemInstruction": {"parts": [{"text": instrucciones}]},
        "contents": [{"role": "user", "parts": [{"text": mensaje}]}],
        "generationConfig": {
            "temperature": 0.2,                     # Baja = respuestas más consistentes
            "responseMimeType": "application/json",  # Le pedimos JSON puro
        },
    }

    datos = _post_con_reintentos(url, headers, cuerpo)
    try:
        partes = datos["candidates"][0]["content"]["parts"]
        return "".join(parte.get("text", "") for parte in partes)
    except (KeyError, IndexError):
        raise ErrorIA(f"Respuesta inesperada de Gemini: {str(datos)[:300]}")


def _consultar_gemini(instrucciones: str, mensaje: str) -> str:
    """Prueba los modelos de GEMINI_MODELO en orden hasta que uno responda."""
    if not GEMINI_KEY:
        raise ErrorIA("Falta GEMINI_API_KEY en el archivo .env")

    ultimo_error = None
    for numero, modelo in enumerate(GEMINI_MODELOS, start=1):
        try:
            return _consultar_un_modelo_gemini(modelo, instrucciones, mensaje)
        except ErrorIA as error:
            ultimo_error = error
            hay_otro = numero < len(GEMINI_MODELOS)
            if error.codigo in CODIGOS_CAMBIAR_MODELO and hay_otro:
                siguiente = GEMINI_MODELOS[numero]
                print(f"   🔁 {modelo} no respondió ({error.codigo}); probando {siguiente}...")
                continue
            raise

    raise ultimo_error or ErrorIA("No hay modelos de Gemini configurados")


def _consultar_claude(instrucciones: str, mensaje: str) -> str:
    """Envía el mensaje a Claude (Anthropic) y regresa el texto de la respuesta."""
    if not ANTHROPIC_KEY:
        raise ErrorIA("Falta ANTHROPIC_API_KEY en el archivo .env")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    cuerpo = {
        "model": CLAUDE_MODELO,
        "max_tokens": 4000,
        "temperature": 0.2,
        "system": instrucciones,
        "messages": [{"role": "user", "content": mensaje}],
    }

    datos = _post_con_reintentos(url, headers, cuerpo)
    return "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")


def consultar_ia(instrucciones: str, mensaje: str) -> str:
    """Punto de entrada único: decide a qué proveedor preguntarle."""
    if PROVEEDOR == "gemini":
        return _consultar_gemini(instrucciones, mensaje)
    if PROVEEDOR == "claude":
        return _consultar_claude(instrucciones, mensaje)
    raise ErrorIA(f"Proveedor desconocido: {PROVEEDOR} (usa gemini o claude)")


def _extraer_json(texto: str):
    """Convierte texto a datos de Python, tolerando errores comunes de la IA.

    - Quita bloques ```json ... ``` si la IA los agregó.
    - Si la IA mandó VARIOS objetos JSON seguidos (error "Extra data"),
      los lee todos uno por uno y regresa una lista con ellos.
    """
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`")
        if texto.lower().startswith("json"):
            texto = texto[4:]
        texto = texto.strip()

    decodificador = json.JSONDecoder()
    objetos = []
    posicion = 0
    while posicion < len(texto):
        # Saltamos espacios, saltos de línea y comas sueltas entre objetos
        while posicion < len(texto) and texto[posicion] in " \t\r\n,":
            posicion += 1
        if posicion >= len(texto):
            break
        # raw_decode lee UN objeto JSON y nos dice dónde terminó
        objeto, posicion = decodificador.raw_decode(texto, posicion)
        objetos.append(objeto)

    if not objetos:
        raise json.JSONDecodeError("Respuesta vacía", texto, 0)
    return objetos[0] if len(objetos) == 1 else objetos


def consultar_ia_json(instrucciones: str, mensaje: str):
    """Igual que consultar_ia, pero convierte la respuesta JSON a datos de Python.

    Si la IA mandó varios objetos JSON seguidos, regresa una lista con todos.
    """
    return _extraer_json(consultar_ia(instrucciones, mensaje))


if __name__ == "__main__":
    # Prueba rápida: python ia.py
    print(f"Proveedor: {PROVEEDOR}")
    if PROVEEDOR == "gemini":
        print(f"Modelos en orden: {', '.join(GEMINI_MODELOS)}")
    respuesta = consultar_ia_json(
        "Responde solo con JSON.",
        'Devuelve {"saludo": "Hey! Listen!", "estado": "funcionando"}',
    )
    print("Respuesta de la IA:", respuesta)
