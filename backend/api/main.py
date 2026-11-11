"""API de predicción de retrasos (ArrDel15) para salidas programadas de los aeropuertos del modelo (IAH, DFW, ORD).

    uvicorn api.main:app --reload --port 8000      # desde backend/

Las variables se construyen igual que en entrenamiento: src.features.agregar_contexto + el pipeline guardado.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware

from api import contexto, explicacion, factores, vuelos
from src.features import GRUPOS_NWS, RANGO_SPC, aeropuertos, agregar_contexto, salida_utc

RAIZ = Path(__file__).resolve().parents[1]
MAX_VENTANA = timedelta(hours=12)  # límite de AeroDataBox
NOMBRE_SPC = {v: k for k, v in RANGO_SPC.items()}

app = FastAPI(title="Retrasos de vuelos (ArrDel15)")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])


def ruta_modelo():
    return Path(os.environ.get("MODELO_PATH", RAIZ / "models" / "modelo.joblib"))


@lru_cache(maxsize=2)
def _cargar(ruta, mtime):  # mtime en la clave: si se reentrena, se recarga solo
    return joblib.load(ruta)


def cargar_modelo():
    r = ruta_modelo()
    return _cargar(str(r), r.stat().st_mtime) if r.exists() else None


def _ventana(desde, hasta):
    if not desde or not hasta:
        raise HTTPException(422, "faltan los parámetros 'desde' y 'hasta' (formato AAAA-MM-DDTHH:MM, hora local)")
    try:
        d0, d1 = datetime.fromisoformat(desde), datetime.fromisoformat(hasta)
    except ValueError:
        raise HTTPException(422, "fecha inválida: usa el formato AAAA-MM-DDTHH:MM (hora local del aeropuerto)") from None
    if d0.tzinfo or d1.tzinfo:
        raise HTTPException(422, "las fechas van en hora local del aeropuerto, sin zona horaria")
    if d0 >= d1:
        raise HTTPException(422, "'desde' debe ser anterior a 'hasta'")
    if d1 - d0 > MAX_VENTANA:
        raise HTTPException(422, "la ventana no puede exceder 12 horas (límite de AeroDataBox)")
    return d0, d1


def _num(x, dec=1):
    return None if pd.isna(x) else round(float(x), dec)


def _lugar(fila, l, nws, aeropuerto):
    rango = fila[f"{l}spc"]
    codigo = fila[f"{l}weather_code"]
    return {"temperatura": _num(fila[f"{l}temperature_2m"]), "precipitacion": _num(fila[f"{l}precipitation"]),
            "nieve": _num(fila[f"{l}snowfall"]), "nubosidad": _num(fila[f"{l}cloud_cover"], 0),
            "viento": _num(fila[f"{l}wind_speed_10m"]), "rafagas": _num(fila[f"{l}wind_gusts_10m"]),
            "cape": _num(fila[f"{l}cape"], 0), "codigo": None if pd.isna(codigo) else int(codigo),
            "spc": NOMBRE_SPC.get(int(rango)) if rango > 0 else None,
            "avisos_nws": contexto.avisos_vigentes(nws, aeropuerto, fila["salida_utc"])}


class FalloClima(Exception):
    """Open-Meteo (o los avisos SPC/NWS) no respondió."""


def predecir(lista, aeropuerto, bundle):
    """Llena probabilidad, predicción y contexto de los vuelos no excluidos (en sitio).
    Devuelve el estado del clima: "ok", "respaldo" (pronóstico actual en lugar de Previous Runs) o None si no se consultó."""
    incluidos = [v for v in lista if not v["excluido"]]
    if not incluidos:
        return None
    df = pd.DataFrame({"salida_local": pd.to_datetime([v["_salida"] for v in incluidos]).astype("datetime64[ns]"),
                       "origen": aeropuerto, "destino": [v["destino"]["iata"] for v in incluidos],
                       "aerolinea": [v["aerolinea"] for v in incluidos]})
    utc = salida_utc(df)
    try:
        clima, spc, nws = contexto.obtener([aeropuerto, *df["destino"]], utc.min(), utc.max())
    except Exception as e:  # red, HTTP o respuesta vacía: todo cuenta como fallo del clima
        raise FalloClima(str(e) or type(e).__name__) from e
    respaldo = clima.attrs.get("respaldo", [])
    ctx = agregar_contexto(df, clima, spc, nws)
    ctx["duracion_prog"] = np.nan  # no existe en vivo; el pipeline estima la duración con medianas
    prob = bundle["modelo"].predict_proba(ctx[bundle["entrada"]])[:, 1]
    fac = factores.calcular(bundle, ctx[bundle["entrada"]])
    for v, p, fx, (_, fila) in zip(incluidos, prob, fac, ctx.iterrows()):
        v["probabilidad"] = round(float(p), 4)
        v["factores"] = fx  # lo que más sube/baja la estimación; no es la causa del retraso
        v["prediccion"] = "retrasado" if p >= bundle["umbral"] else "a_tiempo"
        v["contexto"] = {"origen": _lugar(fila, "o_", nws, aeropuerto),
                         "destino": _lugar(fila, "d_", nws, fila["destino"])}
        v["clima_aproximado"] = aeropuerto in respaldo or fila["destino"] in respaldo
    return "respaldo" if respaldo else "ok"


def _predicciones_cache(aeropuerto, d0, d1):
    """Respuesta procesada más reciente que cubra la ventana (para presentar sin conexión), o None."""
    cubren = []
    for p in vuelos.dir_cache("predicciones").glob(f"{aeropuerto}_*_*.json"):
        a, b = vuelos._ventana_archivo(p)
        if a <= d0 and d1 <= b:
            cubren.append(p)
    if not cubren:
        return None
    r = json.loads(max(cubren, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8"))
    r["vuelos"] = [v for v in r["vuelos"] if d0 <= datetime.fromisoformat(v["salida_local"]) <= d1]
    return {**r, "fuente": "cache"}


@app.get("/api/vuelos")
def listar_vuelos(aeropuerto: str = "IAH", desde: str | None = None, hasta: str | None = None):
    aeropuerto = aeropuerto.upper()
    d0, d1 = _ventana(desde, hasta)
    bundle = cargar_modelo()
    if bundle is None:
        raise HTTPException(503, "modelo no disponible: ejecuta python -m src.entrenar")
    origenes = bundle.get("origenes", ["IAH"])  # modelos viejos solo traían IAH
    if aeropuerto not in origenes:
        raise HTTPException(400, f"aeropuerto no soportado: {aeropuerto} (el modelo se entrenó con salidas de {', '.join(origenes)})")

    # "fallo" dice qué consulta falló ("vuelos" o "clima") para que el frontend lo notifique.
    def sin_datos(paso, e):  # se sirve la última predicción guardada, si la hay
        r = _predicciones_cache(aeropuerto, d0, d1)
        if r is None:
            return JSONResponse(status_code=503, content={"detail": f"sin datos: {e}", "fallo": paso})
        return {**r, "fallo": paso, "fallo_detalle": str(e)}

    try:
        datos, fuente = vuelos.obtener(aeropuerto, d0, d1)
    except (vuelos.SinDatos, OSError) as e:
        return sin_datos("vuelos", e)
    lista = vuelos.poblacion(datos, aeropuerto, set(bundle["aerolineas"]), d0, d1)
    try:
        estado_clima = predecir(lista, aeropuerto, bundle)
    except FalloClima as e:
        return sin_datos("clima", e)

    a = aeropuertos()[aeropuerto]
    for v in lista:
        del v["_salida"]
    respuesta = {
        "aeropuerto": {"iata": aeropuerto, "nombre": a["name"], "lat": a["lat"], "lon": a["lon"], "tz": a["tz"]},
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuente": fuente,
        "clima": estado_clima,
        "modelo": {"nombre": bundle["nombre"], "umbral": round(float(bundle["umbral"]), 4),
                   "provisional": os.environ.get("MODELO_PROVISIONAL") == "1"},
        "vuelos": lista}
    ruta = vuelos.dir_cache("predicciones") / f"{aeropuerto}_{d0:{vuelos.FORMATO}}_{d1:{vuelos.FORMATO}}.json"
    ruta.write_text(json.dumps(respuesta, ensure_ascii=False), encoding="utf-8")
    return respuesta


@app.get("/api/vuelos/{id_vuelo}")
def detalle_vuelo(id_vuelo: str):
    archivos = sorted(vuelos.dir_cache("predicciones").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in archivos:
        for v in json.loads(p.read_text(encoding="utf-8"))["vuelos"]:
            if v["id"] == id_vuelo:
                return v
    raise HTTPException(404, f"vuelo no encontrado en la caché: {id_vuelo} (consulta antes /api/vuelos)")


@app.get("/api/salud")
def salud():
    bundle = cargar_modelo()
    cache = {}
    for sub in ("api", "predicciones", "contexto"):
        archivos = [p for p in vuelos.dir_cache(sub).iterdir() if p.is_file()]
        cache[sub] = {"archivos": len(archivos), "bytes": sum(p.stat().st_size for p in archivos)}
    return {
        "modelo": {"disponible": bundle is not None, "nombre": bundle and bundle["nombre"],
                   "umbral": bundle and round(float(bundle["umbral"]), 4),
                   "entrenado_hasta": bundle and bundle["entrenado_hasta"],
                   "provisional": os.environ.get("MODELO_PROVISIONAL") == "1"},
        "aerodatabox": vuelos.unidades_restantes() or {"unidades": None, "visto": None},
        "open_meteo_previous_error": contexto.PREVIOUS_ERROR[0],
        "cache": cache}


# ---------- Simulador: el modelo evalúa un vuelo con condiciones escritas a mano ----------
AEROLINEAS = {"AA": "American Airlines", "AS": "Alaska Airlines", "B6": "JetBlue", "DL": "Delta Air Lines",
              "F9": "Frontier Airlines", "NK": "Spirit Airlines", "UA": "United Airlines", "WN": "Southwest Airlines"}


class Clima(BaseModel):
    """Pronóstico de un aeropuerto, en las unidades de Open-Meteo con las que se entrenó el modelo."""
    temperatura: float = Field(20, ge=-50, le=55)      # °C
    precipitacion: float = Field(0, ge=0, le=200)      # mm en la hora
    nieve: float = Field(0, ge=0, le=100)              # cm en la hora
    nubosidad: float = Field(0, ge=0, le=100)          # %
    viento: float = Field(10, ge=0, le=250)            # km/h
    rafagas: float = Field(20, ge=0, le=300)           # km/h
    cape: float = Field(0, ge=0, le=10000)             # J/kg
    codigo: int = Field(0, ge=0, le=99)                # código WMO
    spc: int = Field(0, ge=0, le=6)                    # 0 = sin riesgo … 6 = alto
    avisos: dict[str, int] = Field(default_factory=dict)  # grupo NWS -> cuántos avisos vigentes


class Simulacion(BaseModel):
    origen: str
    destino: str
    aerolinea: str
    salida_local: datetime  # hora local del origen, sin zona
    clima_origen: Clima = Clima()
    clima_destino: Clima = Clima()


@app.get("/api/opciones")
def opciones():
    """Lo que acepta el simulador: orígenes, aerolíneas y destinos que vio el modelo."""
    bundle = cargar_modelo()
    if bundle is None:
        raise HTTPException(503, "modelo no disponible: ejecuta python -m src.entrenar")
    db = aeropuertos()
    destinos = [{"iata": d, "nombre": db[d]["name"], "ciudad": db[d]["city"], "lat": db[d]["lat"], "lon": db[d]["lon"]}
                for d in bundle.get("destinos", []) if d in db]
    return {"origenes": bundle.get("origenes", ["IAH"]),
            "aerolineas": [{"codigo": a, "nombre": AEROLINEAS.get(a, a)} for a in bundle["aerolineas"]],
            "destinos": sorted(destinos, key=lambda d: d["ciudad"] or d["iata"]),
            "grupos_nws": [*GRUPOS_NWS, "otro"]}


@app.post("/api/simular")
def simular(s: Simulacion):
    bundle = cargar_modelo()
    if bundle is None:
        raise HTTPException(503, "modelo no disponible: ejecuta python -m src.entrenar")
    origen, destino, aerolinea = s.origen.upper(), s.destino.upper(), s.aerolinea.upper()
    db = aeropuertos()
    if origen not in bundle.get("origenes", ["IAH"]):
        raise HTTPException(400, f"origen no soportado: {origen}")
    if aerolinea not in bundle["aerolineas"]:
        raise HTTPException(400, f"aerolínea no vista en entrenamiento: {aerolinea}")
    if destino not in db or destino == origen:
        raise HTTPException(400, f"destino inválido: {destino}")
    grupos = [*GRUPOS_NWS, "otro"]
    fila = {"salida_local": pd.Timestamp(s.salida_local.replace(tzinfo=None)), "origen": origen, "destino": destino,
            "aerolinea": aerolinea, "duracion_prog": np.nan}
    for l, c in (("o_", s.clima_origen), ("d_", s.clima_destino)):
        if set(c.avisos) - set(grupos):
            raise HTTPException(422, f"grupos NWS válidos: {', '.join(grupos)}")
        fila |= {f"{l}temperature_2m": c.temperatura, f"{l}precipitation": c.precipitacion,
                 f"{l}snowfall": c.nieve, f"{l}cloud_cover": c.nubosidad, f"{l}wind_speed_10m": c.viento,
                 f"{l}wind_gusts_10m": c.rafagas, f"{l}cape": c.cape, f"{l}weather_code": c.codigo,
                 f"{l}spc": c.spc, **{f"{l}nws_{g}": c.avisos.get(g, 0) for g in grupos}}
    X = pd.DataFrame([fila])[bundle["entrada"]]
    p = float(bundle["modelo"].predict_proba(X)[:, 1][0])

    def lugar(c):
        return {"temperatura": c.temperatura, "precipitacion": c.precipitacion, "nieve": c.nieve,
                "nubosidad": c.nubosidad, "viento": c.viento, "rafagas": c.rafagas, "cape": c.cape,
                "codigo": c.codigo, "spc": NOMBRE_SPC.get(c.spc), "avisos_nws": [g for g, n in c.avisos.items() if n]}

    d = db[destino]
    return {"id": f"SIM-{aerolinea}-{destino}-{s.salida_local:%Y%m%d%H%M}", "numero": f"{aerolinea} (simulado)",
            "aerolinea": aerolinea, "aerolinea_nombre": AEROLINEAS.get(aerolinea, aerolinea), "simulado": True,
            "destino": {"iata": destino, "nombre": d["name"], "lat": d["lat"], "lon": d["lon"]},
            "salida_local": s.salida_local.strftime("%Y-%m-%dT%H:%M"),
            "probabilidad": round(p, 4), "prediccion": "retrasado" if p >= bundle["umbral"] else "a_tiempo",
            "factores": factores.calcular(bundle, X)[0],
            "contexto": {"origen": lugar(s.clima_origen), "destino": lugar(s.clima_destino)}}


# ---------- Explicación en lenguaje natural (modelo de lenguaje gratuito vía OpenRouter) ----------
class PeticionExplicacion(BaseModel):
    origen: str
    vuelo: dict  # el mismo objeto de vuelo que devuelven /api/vuelos o /api/simular


@app.post("/api/explicar")
def explicar(p: PeticionExplicacion):
    vuelos.cargar_env()  # AI_KEY y AI_MODELO
    try:
        texto, modelo = explicacion.explicar(p.vuelo, p.origen.upper())
    except explicacion.ErrorIA as e:
        raise HTTPException(502, str(e)) from None
    except (KeyError, TypeError):
        raise HTTPException(422, "vuelo incompleto: falta destino o probabilidad") from None
    return {"texto": texto, "modelo": modelo}


# ---------- Frontend compilado (npm run build): mismo origen que la API, sin CORS en producción ----------
_DIST = RAIZ.parent / "frontend" / "dist"
if _DIST.exists():  # al final: las rutas /api/* ya están registradas y tienen prioridad
    from fastapi.staticfiles import StaticFiles
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
