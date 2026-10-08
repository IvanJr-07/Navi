"""
juez.py — El juez de Navi.
Le pasa las publicaciones del recolector a la IA, junto con tu perfil
de gustos y tus reacciones 👍 / 👎 recientes, y la IA califica del 0 al 10
qué tanto te interesa cada una.

Uso:
    python juez.py   -> prueba: muestra calificaciones, no envía ni guarda nada
                        (los avisos reales los manda navi.py)
"""
import json
import os
import time

from ia import ErrorIA, consultar_ia_json
from memoria import ejemplos_feedback
from recolector import recolectar

ARCHIVO_INTERESES = "intereses.yaml"
TAMANO_LOTE = 20          # Publicaciones por petición a la IA
PAUSA_ENTRE_LOTES = 6     # Segundos entre peticiones (límites del nivel gratis)
INTENTOS_POR_LOTE = 2     # Si la IA responde con JSON roto, se reintenta una vez
UMBRAL_AVISO = 7          # Calificación mínima para avisarte
MAX_AVISOS = 10           # Máximo de mensajes por ejecución (para no saturarte)

INSTRUCCIONES = """Eres Navi, el asistente que avisa a Ivan de cosas que le interesan.
Recibirás el perfil de gustos de Ivan y una lista de publicaciones (noticias, ofertas,
anuncios, publicaciones de foros) en formato JSON, cada una con un "id".

Califica cada publicación del 0 al 10 según qué tanto le interesaría a Ivan:
- 9-10: Imperdible. Ediciones limitadas o coleccionables de sus sagas o bandas favoritas,
  anuncio de un juego nuevo de sus sagas, álbum o vinilo especial de sus bandas favoritas,
  una oferta excepcional en algo de su lista.
- 7-8: Muy probablemente le interesa. Noticia importante y concreta sobre algo de su lista:
  un anuncio, lanzamiento, fecha de salida u oferta.
- 4-6: Relacionado de lejos (misma plataforma o género, pero no algo de su lista).
- 0-3: No le interesa.

Reglas:
- Sé estricto: es mejor no avisar que molestarlo con cosas irrelevantes.
- Solo valen 7 o más los ANUNCIOS, LANZAMIENTOS, EDICIONES ESPECIALES y OFERTAS.
- Preguntas, discusiones, memes, fotos o videos personales y pedidos de consejo
  publicados en foros (como Reddit) valen 0-4, aunque el tema le guste.
- Reseñas, guías, listas de recomendaciones y artículos de opinión valen 0-5.
- Productos genéricos (cargadores, teclados, membresías, accesorios) valen 0-4 aunque
  tengan temática o marca gamer, salvo que sean de una saga o banda de su lista.
- Que una publicación sea de una PLATAFORMA de su lista (Switch 2, PC) NO basta para 7+.
  Un juego que no está en su lista vale 0-5 aunque sea de esa plataforma o esté en oferta.
  Solo el hardware de edición especial o un paquete de consola relevante para él puede valer 7+.
- Chismes, fotos, entrevistas y rumores sobre artistas valen 0-5. Solo valen 7+ los anuncios
  confirmados: álbum o sencillo nuevo, gira, vinilo o edición especial.
- Si el perfil indica dónde vive Ivan, los lanzamientos o ediciones exclusivas de otras
  regiones valen 0-5, salvo que sean coleccionables de una saga o banda de su lista.
- Respeta la sección "no_me_interesa": esas cosas valen 0-3.
- Si recibes "REACCIONES RECIENTES DE IVAN", úsalas para afinar tu criterio:
  califica más bajo lo parecido a lo que marcó con 👎 y más alto lo parecido a lo que marcó con 👍.
  Piensa en el TIPO de publicación (por ejemplo "soundtracks no, estatuas sí"), no solo en esa noticia.
- Escribe "razon" y "resumen_es" en español.
- En "razon" háblale directamente a Ivan de TÚ, como una amiga que le avisa algo.
  Ejemplo: "Es una edición limitada de Sonic, una de tus sagas favoritas."
  Nunca uses tercera persona ("sus sagas", "le interesa").

Responde SOLO con UN objeto JSON, con este formato exacto:
{"evaluaciones": [
  {"id": 0, "puntuacion": 8, "razon": "Por qué te interesa o no, hablándole de tú (máximo 15 palabras)",
   "resumen_es": "Resumen de la publicación en una línea, en español"}
]}
Incluye TODAS las publicaciones que recibas, una por id, dentro de la misma lista."""


def cargar_intereses() -> str:
    """Lee tu perfil de gustos, sin los comentarios (ahorra tokens).

    En la nube, el perfil viene del secreto INTERESES de GitHub (así no queda
    público en el repositorio). En tu PC, se lee del archivo intereses.yaml.
    """
    texto = os.getenv("INTERESES")
    if not texto:
        with open(ARCHIVO_INTERESES, encoding="utf-8") as archivo:
            texto = archivo.read()
    lineas = [l for l in texto.splitlines() if not l.strip().startswith("#")]
    return "\n".join(lineas).strip()


def formatear_reacciones(ejemplos) -> str:
    """Convierte tus 👍 / 👎 guardados en texto para el prompt (aprendizaje con ejemplos)."""
    if not ejemplos:
        return ""
    lineas = []
    for e in ejemplos:
        marca = "👍 LE GUSTÓ" if e["feedback"] > 0 else "👎 NO LE GUSTÓ"
        lineas.append(f'- {marca}: "{e["titulo"]}" (antes le habías dado {e["puntuacion"]}/10)')
    return "REACCIONES RECIENTES DE IVAN A AVISOS ANTERIORES:\n" + "\n".join(lineas) + "\n\n"


def armar_mensaje(intereses: str, lote: list[dict], reacciones: str = "") -> str:
    """Construye el mensaje que recibe la IA: tus gustos + tus reacciones + las publicaciones."""
    publicaciones = [
        {"id": i, "fuente": p["fuente"], "titulo": p["titulo"], "resumen": p["resumen"]}
        for i, p in enumerate(lote)
    ]
    return (
        f"PERFIL DE GUSTOS DE IVAN:\n{intereses}\n\n"
        f"{reacciones}"
        f"PUBLICACIONES A EVALUAR:\n{json.dumps(publicaciones, ensure_ascii=False, indent=1)}"
    )


def a_entero(valor, por_defecto: int = 0) -> int:
    """Convierte la calificación a entero aunque la IA la mande como texto."""
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return por_defecto


def extraer_evaluaciones(respuesta) -> list[dict]:
    """Saca la lista de evaluaciones sin importar en qué forma la mandó la IA.

    Acepta:
      {"evaluaciones": [...]}                      -> lo normal
      [ {"evaluaciones": [...]}, {...} ]           -> varios objetos seguidos
      [ {"id": 0, ...}, {"id": 1, ...} ]           -> la lista directa
    """
    if isinstance(respuesta, dict):
        if "evaluaciones" in respuesta:
            return list(respuesta["evaluaciones"])
        if "id" in respuesta:
            return [respuesta]
        return []

    evaluaciones = []
    if isinstance(respuesta, list):
        for elemento in respuesta:
            evaluaciones.extend(extraer_evaluaciones(elemento))
    return evaluaciones


def evaluar_lote(intereses: str, lote: list[dict], reacciones: str = "") -> list[dict]:
    """Manda un lote a la IA (con un reintento si el JSON viene roto)."""
    mensaje = armar_mensaje(intereses, lote, reacciones)

    for intento in range(1, INTENTOS_POR_LOTE + 1):
        try:
            return extraer_evaluaciones(consultar_ia_json(INSTRUCCIONES, mensaje))
        except json.JSONDecodeError as error:
            if intento < INTENTOS_POR_LOTE:
                print(f"   ⚠️ JSON inválido ({error}); reintentando el lote...")
                time.sleep(PAUSA_ENTRE_LOTES)
                continue
            raise
    return []


def evaluar(publicaciones: list[dict]) -> list[dict]:
    """Manda las publicaciones a la IA por lotes y regresa cada una con su calificación."""
    intereses = cargar_intereses()
    ejemplos = ejemplos_feedback()
    reacciones = formatear_reacciones(ejemplos)
    if ejemplos:
        print(f"📚 Usando {len(ejemplos)} reacciones tuyas como ejemplos.")
    lotes = [publicaciones[i:i + TAMANO_LOTE] for i in range(0, len(publicaciones), TAMANO_LOTE)]
    evaluadas = []

    for numero, lote in enumerate(lotes, start=1):
        if numero > 1:
            time.sleep(PAUSA_ENTRE_LOTES)
        print(f"🧠 Evaluando lote {numero}/{len(lotes)} ({len(lote)} publicaciones)...")

        try:
            resultados = evaluar_lote(intereses, lote, reacciones)
        except (ErrorIA, json.JSONDecodeError) as error:
            print(f"   ❌ Falló este lote: {error}")
            continue

        ids_vistos = set()
        for resultado in resultados:
            if not isinstance(resultado, dict):
                continue
            indice = a_entero(resultado.get("id"), -1)
            if 0 <= indice < len(lote) and indice not in ids_vistos:
                ids_vistos.add(indice)
                publicacion = dict(lote[indice])  # Copia para no modificar el original
                publicacion["puntuacion"] = a_entero(resultado.get("puntuacion"))
                publicacion["razon"] = resultado.get("razon", "")
                publicacion["resumen_es"] = resultado.get("resumen_es", "")
                evaluadas.append(publicacion)

        faltantes = len(lote) - len(ids_vistos)
        if faltantes:
            print(f"   ⚠️ La IA no calificó {faltantes} publicaciones de este lote.")

    return evaluadas


if __name__ == "__main__":
    print("🧚 Navi está revisando las fuentes...\n")
    publicaciones = recolectar()
    print(f"\n{len(publicaciones)} publicaciones. Pasándolas al juez...\n")

    evaluadas = evaluar(publicaciones)
    evaluadas.sort(key=lambda p: p["puntuacion"], reverse=True)
    relevantes = [p for p in evaluadas if p["puntuacion"] >= UMBRAL_AVISO]

    print(f"\n🏆 Las 15 mejor calificadas de {len(evaluadas)} evaluadas:\n")
    for p in evaluadas[:15]:
        marca = "🔔" if p["puntuacion"] >= UMBRAL_AVISO else "  "
        print(f"{marca} {p['puntuacion']:>2}/10  [{p['fuente']}] {p['titulo']}")
        print(f"          → {p['razon']}")

    print(f"\n{len(relevantes)} publicaciones pasan el umbral de {UMBRAL_AVISO}/10.")
    print("(Prueba: no se envió ni se guardó nada.)")
