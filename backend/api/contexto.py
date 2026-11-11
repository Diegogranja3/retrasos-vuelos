"""Contexto en vivo para agregar_contexto: clima pronosticado, panorama SPC Día 2 y avisos del NWS.

Mismas fuentes y columnas que en entrenamiento (src/datos.py). Caché en data/cache/contexto/ con vigencia de 1 h;
si una descarga falla se usa la copia vieja si existe.

Clima (Open-Meteo Previous Runs API, siempre en GMT): para cada hora válida t
- si t <= ahora + 24 h se usa *_previous_day1 (pronóstico emitido >= 24 h antes, como en entrenamiento);
- si t >  ahora + 24 h se usa la corrida actual (sin sufijo), que ya tiene >= 24 h de anticipación.
"""
import io
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from api.vuelos import dir_cache
from src.datos import SUFIJO, URL_CLIMA, URL_NWS, URL_SPC, VARS_CLIMA
from src.features import ANTICIPACION, aeropuertos

VIGENCIA = 3600
LOTE_CLIMA = 50  # ubicaciones por llamada a Open-Meteo
URL_CLIMA_RESPALDO = "https://api.open-meteo.com/v1/forecast"  # si Previous Runs no responde


def leer_url(url, intentos=3, timeout=60):
    # El IEM a veces corta respuestas; se reintenta poco para no colgar el servidor sin red.
    for i in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                texto = r.read().decode("utf-8")
            if not texto.strip():  # el IEM a veces responde 200 sin cuerpo; no se guarda en caché
                raise ValueError("respuesta vacía")
            return texto
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(1 + i)


def _fresco(ruta):
    return ruta.exists() and time.time() - ruta.stat().st_mtime < VIGENCIA


def _con_cache(ruta, descargar):
    """Texto desde caché vigente; si no, descarga. Si la descarga falla, usa la copia vieja si existe."""
    if _fresco(ruta):
        return ruta.read_text(encoding="utf-8")
    try:
        texto = descargar()
    except Exception:
        if ruta.exists():
            return ruta.read_text(encoding="utf-8")
        raise
    ruta.write_text(texto, encoding="utf-8")
    return texto


def _clima(codigos, inicio, fin):
    d, db = dir_cache("contexto"), aeropuertos()
    ruta = {c: d / f"clima_{c}_{inicio}_{fin}.json" for c in codigos}
    faltan = [c for c in codigos if not _fresco(ruta[c])]
    for k in range(0, len(faltan), LOTE_CLIMA):  # varias ubicaciones por llamada
        lote = faltan[k:k + LOTE_CLIMA]
        q = {"latitude": ",".join(str(db[c]["lat"]) for c in lote),
             "longitude": ",".join(str(db[c]["lon"]) for c in lote),
             "hourly": ",".join(v + s for v in VARS_CLIMA for s in ("", SUFIJO)),
             "start_date": inicio, "end_date": fin, "timezone": "GMT"}
        respaldo = False
        try:  # un intento corto: si Previous Runs se cuelga, se pasa rápido al respaldo
            r = json.loads(leer_url(f"{URL_CLIMA}?{urllib.parse.urlencode(q)}", intentos=1, timeout=10))
        except Exception:
            if all(ruta[c].exists() for c in lote):
                continue  # se usa la copia vieja
            # ponytail: respaldo = corrida actual de la API principal también para *_previous_day1.
            # Exacto para t > ahora + 24 h; para t <= 24 h es un pronóstico más reciente que el del entrenamiento.
            q["hourly"] = ",".join(VARS_CLIMA)
            r = json.loads(leer_url(f"{URL_CLIMA_RESPALDO}?{urllib.parse.urlencode(q)}"))
            respaldo = True
        for c, x in zip(lote, r if isinstance(r, list) else [r]):
            h = x["hourly"]
            if respaldo:
                h |= {v + SUFIJO: h[v] for v in VARS_CLIMA} | {"respaldo": True}
            ruta[c].write_text(json.dumps(h), encoding="utf-8")

    ahora = pd.Timestamp.now(tz="UTC").tz_localize(None)
    partes = []
    con_respaldo = []
    for c in codigos:
        crudo = json.loads(ruta[c].read_text(encoding="utf-8"))
        if crudo.pop("respaldo", False):
            con_respaldo.append(c)
        h = pd.DataFrame(crudo)
        h["time"] = pd.to_datetime(h["time"]).astype("datetime64[ns]")
        lejos = h["time"] > ahora + ANTICIPACION
        for v in VARS_CLIMA:
            h[v] = h[v + SUFIJO].where(~lejos, h[v])
        partes.append(h[["time", *VARS_CLIMA]].assign(aeropuerto=c))
    clima = pd.concat(partes, ignore_index=True)
    clima.attrs["respaldo"] = con_respaldo
    return clima


def _spc(c):
    db = aeropuertos()
    q = urllib.parse.urlencode({"lat": db[c]["lat"], "lon": db[c]["lon"], "day": 2, "last": 50, "fmt": "csv"})
    texto = _con_cache(dir_cache("contexto") / f"spc_{c}.csv", lambda: leer_url(f"{URL_SPC}?{q}"))
    try:
        df = pd.read_csv(io.StringIO(texto))
    except pd.errors.EmptyDataError:
        df = pd.DataFrame(columns=["threshold", "utc_issue", "utc_expire", "utc_product_issue"])
    return df.assign(aeropuerto=c)


def _nws(c, inicio, fin):
    db = aeropuertos()
    q = urllib.parse.urlencode({"lat": db[c]["lat"], "lon": db[c]["lon"], "sdate": inicio, "edate": fin})
    texto = _con_cache(dir_cache("contexto") / f"nws_{c}_{inicio}_{fin}.json", lambda: leer_url(f"{URL_NWS}?{q}"))
    df = pd.DataFrame(json.loads(texto)["events"],
                      columns=["phenomena", "significance", "name", "issue", "expire"])
    return df.assign(aeropuerto=c)


def obtener(codigos, utc_min, utc_max):
    """(clima, spc, nws) para los aeropuertos dados y salidas UTC en [utc_min, utc_max], listos para agregar_contexto."""
    codigos = sorted(set(codigos))
    hoy = pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    # Clima: un día de margen por el redondeo a la hora. NWS: desde 3 días antes hasta 2 después.
    clima = _clima(codigos, (utc_min - pd.Timedelta(days=1)).date().isoformat(),
                   (utc_max + pd.Timedelta(days=1)).date().isoformat())
    n0 = (min(hoy, utc_min.normalize()) - pd.Timedelta(days=3)).date().isoformat()
    n1 = (max(hoy, utc_max.normalize()) + pd.Timedelta(days=2)).date().isoformat()
    # El IEM tarda ~2 s por consulta de SPC; 24 a la vez: 60 aeropuertos en ~9 s (con 8 eran ~16 s, con 48 no mejora)
    with ThreadPoolExecutor(max_workers=24) as ex:
        spc = ex.map(_spc, codigos)
        nws = ex.map(lambda c: _nws(c, n0, n1), codigos)
        spc, nws = list(spc), list(nws)
    spc = pd.concat(spc, ignore_index=True)
    for col in ("utc_issue", "utc_expire", "utc_product_issue"):
        spc[col] = pd.to_datetime(spc[col]).astype("datetime64[ns]")
    nws = pd.concat(nws, ignore_index=True)
    for col in ("issue", "expire"):
        nws[col] = pd.to_datetime(nws[col], utc=True).dt.tz_localize(None).astype("datetime64[ns]")
    return clima, spc, nws[["aeropuerto", "phenomena", "significance", "name", "issue", "expire"]]


def avisos_vigentes(nws, aeropuerto, salida_utc):
    """Nombres de los avisos que cuentan con la MISMA regla que src.features._nws:
    emitidos >= 24 h antes de la salida y vigentes a la salida."""
    e = nws[nws["aeropuerto"] == aeropuerto]
    desde = e["issue"] + ANTICIPACION
    ok = (desde < e["expire"]) & (desde <= salida_utc) & (salida_utc < e["expire"])
    return sorted(set(e.loc[ok, "name"].dropna()))
