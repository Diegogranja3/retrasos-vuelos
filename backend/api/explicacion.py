"""Explicación en lenguaje natural de una predicción, redactada por un modelo de lenguaje gratuito (OpenRouter).

El modelo de lenguaje NO predice: solo pone en palabras la probabilidad y los factores que ya calculó la
regresión logística. La instrucción le prohíbe inventar causas o datos.
Llave: AI_KEY en .env (nunca se imprime). Modelo: AI_MODELO en .env (opcional).
"""
import json
import os
import urllib.error
import urllib.request
from functools import lru_cache

from api.factores import WMO

URL = "https://openrouter.ai/api/v1/chat/completions"
# ponytail: modelos gratuitos vistos en /api/v1/models el 2026-11-02; cambian seguido, por eso hay respaldos.
# Nemotron Lightning con el razonamiento apagado respondió en ~2 s; Gemma suele estar saturado (429) en el plan gratuito.
RESPALDOS = ["nvidia/nemotron-3.5-lightning:free", "google/gemma-4-31b-it:free", "openrouter/free"]


def modelos():
    preferido = os.environ.get("AI_MODELO")  # se lee aquí: .env se carga después de importar este módulo
    return [preferido, *[m for m in RESPALDOS if m != preferido]] if preferido else RESPALDOS


INSTRUCCIONES = """Eres el asistente de una aplicación que predice retrasos de vuelos. Explicas, en español de México y para cualquier persona, la predicción de un modelo de aprendizaje automático (regresión logística) sobre si un vuelo llegará con 15 minutos o más de retraso.
Datos que recibes:
- "factores_que_suben" y "factores_que_bajan": lo que más mueve la estimación.
- "efecto_de_cada_variable": las variables con su efecto real en la estimación ("sube", "baja" y qué tanto: fuerte, moderado o leve). Úsalo para comentar otros parámetros además de los principales.
- "pronostico": el clima pronosticado en origen y destino.
Reglas:
- Usa SOLO estos datos. No inventes causas, cifras, eventos ni datos del clima.
- Di que son factores que mueven la estimación del modelo, no la causa confirmada del retraso.
- Empieza con la probabilidad y los 2 o 3 factores principales. Después comenta 2 o 3 parámetros más de "efecto_de_cada_variable" que no estén entre los principales (por ejemplo, que algo apenas influye o que ayuda un poco).
- El efecto de una variable sale únicamente de "efecto_de_cada_variable"; el "pronostico" sirve para describir el clima.
- Si hay lluvia, nieve, tormenta, niebla o viento fuerte, explícalo en términos sencillos.
- Entre 4 y 6 oraciones, tono claro y amable, sin listas, sin títulos ni formato markdown."""


class ErrorIA(Exception):
    pass


def _efecto(f):
    """'Sale a las 17:00: sube (fuerte)'. El peso está en log-momios; los cortes solo dan una idea de tamaño."""
    p = abs(f["peso"])
    tam = "fuerte" if p >= 0.3 else "moderado" if p >= 0.1 else "leve" if p >= 0.03 else "casi nulo"
    return f"{f['texto']}: {'sube' if f['peso'] > 0 else 'baja'} el riesgo ({tam})"


def _clima_texto(c):
    """Pronóstico de un aeropuerto en una línea legible (evita que el modelo de lenguaje confunda origen y destino)."""
    if not c:
        return "sin datos"
    codigo = c.get("codigo")
    partes = [WMO.get(int(codigo), f"código {codigo}") if codigo is not None else None,
              f"{c.get('temperatura')} °C", f"lluvia {c.get('precipitacion')} mm/h", f"nieve {c.get('nieve', 0)} cm/h",
              f"nubosidad {c.get('nubosidad')} %" if c.get("nubosidad") is not None else None,
              f"viento {c.get('viento')} km/h" if c.get("viento") is not None else None,
              f"ráfagas {c.get('rafagas')} km/h", f"riesgo de tormentas SPC: {c.get('spc') or 'ninguno'}",
              f"avisos del NWS: {', '.join(c['avisos_nws'])}" if c.get("avisos_nws") else "sin avisos del NWS"]
    return ", ".join(x for x in partes if x)


def _resumen(v, origen):
    """Solo lo necesario para redactar (sin ids ni datos internos)."""
    return {
        "vuelo": v.get("numero"), "aerolinea": v.get("aerolinea_nombre") or v.get("aerolinea"),
        "origen": origen, "destino": f"{v['destino'].get('nombre')} ({v['destino'].get('iata')})",
        "salida_local": v.get("salida_local"), "simulado": bool(v.get("simulado")),
        "probabilidad_retraso": f"{round(v['probabilidad'] * 100)} %",
        "factores_que_suben": [f["texto"] for f in (v.get("factores") or {}).get("suben", [])],
        "factores_que_bajan": [f["texto"] for f in (v.get("factores") or {}).get("bajan", [])],
        "efecto_de_cada_variable": [_efecto(f) for f in (v.get("factores") or {}).get("detalle", [])],
        "pronostico": {f"{origen} (origen)": _clima_texto((v.get("contexto") or {}).get("origen")),
                       f"{v['destino'].get('iata')} (destino)": _clima_texto((v.get("contexto") or {}).get("destino"))},
    }


@lru_cache(maxsize=256)
def _pedir(datos_json):
    llave = os.environ.get("AI_KEY")
    if not llave:
        raise ErrorIA("falta AI_KEY en .env")
    ultimo = None
    for modelo in modelos():
        cuerpo = json.dumps({
            "model": modelo, "temperature": 0.2, "max_tokens": 700,
            # Los modelos de razonamiento gastan los tokens "pensando" y devuelven la respuesta vacía.
            "reasoning": {"enabled": False},
            "messages": [{"role": "system", "content": INSTRUCCIONES},
                         {"role": "user", "content": f"Datos de la predicción (JSON):\n{datos_json}"}],
        }).encode("utf-8")
        req = urllib.request.Request(URL, data=cuerpo, method="POST", headers={
            "Authorization": f"Bearer {llave}", "Content-Type": "application/json",
            "X-Title": "Retrasos de vuelos (UANL)"})
        try:
            # 25 s por modelo: si el gratuito está saturado, conviene pasar al respaldo en vez de esperar.
            with urllib.request.urlopen(req, timeout=25) as r:
                texto = (json.load(r)["choices"][0]["message"].get("content") or "").strip()
            if texto:
                return texto, modelo
            ultimo = f"{modelo}: respuesta vacía"
        except urllib.error.HTTPError as e:  # 429 = límite del plan gratuito; se prueba el respaldo
            ultimo = f"{modelo}: HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
            ultimo = f"{modelo}: {type(e).__name__}"
    raise ErrorIA(f"no se pudo generar la explicación ({ultimo})")


def explicar(vuelo, origen):
    """(texto, modelo usado). Se guarda en memoria: el mismo vuelo no vuelve a gastar peticiones."""
    if vuelo.get("probabilidad") is None:
        raise ErrorIA("el vuelo no tiene predicción")
    return _pedir(json.dumps(_resumen(vuelo, origen), ensure_ascii=False, sort_keys=True))
