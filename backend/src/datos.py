"""Descarga y limpieza de datos para el entrenamiento (Fase 1).

    python -m src.datos bts     --aeropuertos IAH,DFW --desde 2024-09 --hasta 2026-08
    python -m src.datos clima
    python -m src.datos riesgos

- bts:   BTS "Marketing Carrier On-Time Performance", solo salidas de los aeropuertos, sin cancelados/desviados.
         Un parquet por mes en data/raw/bts_mkt/ (el ZIP se borra al terminar) y todo junto en data/processed/vuelos.parquet.
- clima: pronóstico emitido 24 h antes (Open-Meteo Previous Runs API, *_previous_day1), en UTC, para los orígenes
         y todos los destinos de data/processed/vuelos.parquet. Un parquet por aeropuerto en data/raw/clima/ (reanudable) y data/processed/clima.parquet.
- riesgos: fenómenos peligrosos desde el archivo del Iowa Environmental Mesonet (IEM), la misma fuente que se usa en vivo:
         * Panorama convectivo SPC Día 2 (riesgo de tormentas severas/tornados) -> data/processed/spc.parquet
         * Vigilancias/avisos del NWS por punto (huracán, invierno, inundación, ...) -> data/processed/nws.parquet
         Que se publicaran >= 24 h antes de la salida se exige al construir las variables (src/features.py).
"""
import argparse
import json
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import airportsdata
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
URL_BTS = ("https://transtats.bts.gov/PREZIP/"
           "On_Time_Marketing_Carrier_On_Time_Performance_Beginning_January_2018_{a}_{m}.zip")
URL_CLIMA = "https://previous-runs-api.open-meteo.com/v1/forecast"
VARS_CLIMA = ["temperature_2m", "precipitation", "snowfall", "cloud_cover",
              "wind_speed_10m", "wind_gusts_10m", "weather_code", "cape"]
SUFIJO = "_previous_day1"

# Solo columnas conocidas antes del despegue + etiqueta + columnas para filtrar/deduplicar.
COLS = {"FlightDate": "fecha", "IATA_Code_Marketing_Airline": "aerolinea",
        "IATA_Code_Operating_Airline": "operadora", "Flight_Number_Operating_Airline": "num_operadora",
        "Origin": "origen", "Dest": "destino", "CRSDepTime": "hhmm", "CRSElapsedTime": "duracion_prog",
        "ArrDel15": "ArrDel15", "Cancelled": "Cancelled", "Diverted": "Diverted"}


def meses(desde, hasta):
    return pd.period_range(desde, hasta, freq="M")


def bts_mes(periodo, aeropuertos):
    """Salidas de los aeropuertos en un mes. El ZIP se baja una vez y se guarda un parquet por aeropuerto."""
    rutas = {a: DATA / "raw" / "bts_mkt" / f"{a}_{periodo}.parquet" for a in aeropuertos}
    if all(r.exists() for r in rutas.values()):
        return pd.concat([pd.read_parquet(r) for r in rutas.values()], ignore_index=True)
    carpeta = DATA / "raw" / "bts_mkt"
    carpeta.mkdir(parents=True, exist_ok=True)
    zpath = carpeta / f"{periodo}.zip"
    urllib.request.urlretrieve(URL_BTS.format(a=periodo.year, m=periodo.month), zpath)
    with zipfile.ZipFile(zpath) as z:
        csv = next(n for n in z.namelist() if n.endswith(".csv"))
        # algunos encabezados traen espacios ("Operating_Airline "), por eso se limpian
        df = pd.read_csv(z.open(csv), usecols=lambda c: c.strip() in COLS, dtype={"CRSDepTime": "int32"})
    df.columns = [COLS[c.strip()] for c in df.columns]
    df = df[df["origen"].isin(aeropuertos)]
    for a, r in rutas.items():
        df[df["origen"] == a].to_parquet(r, index=False)
    zpath.unlink()
    return df


def bts(aeropuertos, desde, hasta):
    partes = []
    for p in meses(desde, hasta):
        df = bts_mes(p, aeropuertos)
        print(f"{p}: {df['origen'].value_counts().to_dict()}", flush=True)
        partes.append(df)
    df = pd.concat(partes, ignore_index=True)
    n0 = len(df)
    df = df[(df["Cancelled"] == 0) & (df["Diverted"] == 0)].drop(columns=["Cancelled", "Diverted"])
    dup = df.duplicated(["fecha", "operadora", "num_operadora", "origen"])
    df = df[~dup].dropna(subset=["ArrDel15"])
    df["ArrDel15"] = df["ArrDel15"].astype(int)
    # Hora local programada (CRSDepTime hhmm; 2400 = medianoche siguiente).
    df["salida_local"] = (pd.to_datetime(df["fecha"]) + pd.to_timedelta(df["hhmm"] // 100, "h")
                          + pd.to_timedelta(df["hhmm"] % 100, "min"))
    df = df.drop(columns=["fecha", "hhmm"])
    print(f"Total {n0} -> {len(df)} tras quitar cancelados/desviados ({n0 - len(df) - dup.sum()}) "
          f"y duplicados ({dup.sum()}). Tasa de retraso: {df['ArrDel15'].mean():.3f}")
    out = DATA / "processed" / "vuelos.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"Guardado: {out}")


def clima_aeropuerto(codigo, lat, lon, inicio, fin):
    """Serie horaria UTC de un aeropuerto; se pide por tramos de ~3 meses para no exceder límites."""
    destino = DATA / "raw" / "clima" / f"{codigo}_{inicio}_{fin}.parquet"
    if destino.exists():
        return pd.read_parquet(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    partes = []
    for t0 in pd.date_range(inicio, fin, freq="90D"):
        t1 = min(t0 + pd.Timedelta(days=89), pd.Timestamp(fin))
        q = {"latitude": lat, "longitude": lon, "hourly": ",".join(v + SUFIJO for v in VARS_CLIMA),
             "start_date": t0.date().isoformat(), "end_date": t1.date().isoformat(), "timezone": "GMT"}
        for intento in range(5):
            try:
                with urllib.request.urlopen(f"{URL_CLIMA}?{urllib.parse.urlencode(q)}", timeout=120) as r:
                    h = pd.DataFrame(json.load(r)["hourly"])
                break
            except urllib.error.HTTPError as e:
                if e.code != 429 or intento == 4:
                    raise
                time.sleep(65)  # límite por minuto de Open-Meteo
            except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
                # conexión cortada o respuesta vacía/no-JSON (ValueError) cuando el servidor está saturado
                if intento == 4:
                    raise
                time.sleep(30)
        partes.append(h)
        time.sleep(1)
    h = pd.concat(partes, ignore_index=True)
    h.columns = [c.removesuffix(SUFIJO) for c in h.columns]
    h["time"] = pd.to_datetime(h["time"])
    h["aeropuerto"] = codigo
    h.to_parquet(destino, index=False)
    return h


def clima():
    vuelos = pd.read_parquet(DATA / "processed" / "vuelos.parquet")
    db = airportsdata.load("IATA")
    codigos = sorted(set(vuelos["destino"]) | set(vuelos["origen"]))
    faltan = [c for c in codigos if c not in db]
    if faltan:
        print(f"Sin coordenadas en airportsdata (se omiten): {faltan}")
    # +-1 día de margen por la conversión local -> UTC
    inicio = (vuelos["salida_local"].min() - pd.Timedelta(days=1)).date().isoformat()
    fin = (vuelos["salida_local"].max() + pd.Timedelta(days=2)).date().isoformat()
    partes = []
    for i, c in enumerate(x for x in codigos if x in db):
        partes.append(clima_aeropuerto(c, db[c]["lat"], db[c]["lon"], inicio, fin))
        print(f"[{i + 1}/{len(codigos)}] {c}", flush=True)
    df = pd.concat(partes, ignore_index=True)
    print("Fracción de nulos:\n" + df[VARS_CLIMA].isna().mean().round(4).to_string())
    df.to_parquet(DATA / "processed" / "clima.parquet", index=False)


URL_SPC = "https://mesonet.agron.iastate.edu/json/spcoutlook.py"
URL_NWS = "https://mesonet.agron.iastate.edu/json/vtec_events_bypoint.py"


def leer_url(url, intentos=4):
    # El IEM a veces corta respuestas largas (IncompleteRead); se reintenta.
    for i in range(intentos):
        try:
            with urllib.request.urlopen(url, timeout=180) as r:
                return r.read().decode("utf-8")
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(5 * (i + 1))


def riesgos_aeropuerto(codigo, lat, lon, inicio, fin):
    cache = DATA / "raw" / "riesgos"
    cache.mkdir(parents=True, exist_ok=True)
    f_spc, f_nws = cache / f"spc_{codigo}.csv", cache / f"nws_{codigo}_{inicio}_{fin}.json"
    if not f_spc.exists():
        q = urllib.parse.urlencode({"lat": lat, "lon": lon, "day": 2, "last": 5000, "fmt": "csv"})
        f_spc.write_text(leer_url(f"{URL_SPC}?{q}"), encoding="utf-8")
    if not f_nws.exists():
        q = urllib.parse.urlencode({"lat": lat, "lon": lon, "sdate": inicio, "edate": fin})
        f_nws.write_text(leer_url(f"{URL_NWS}?{q}"), encoding="utf-8")
    spc = pd.read_csv(f_spc).assign(aeropuerto=codigo)
    nws = pd.DataFrame(json.loads(f_nws.read_text(encoding="utf-8"))["events"]).assign(aeropuerto=codigo)
    return spc, nws


def riesgos():
    vuelos = pd.read_parquet(DATA / "processed" / "vuelos.parquet")
    db = airportsdata.load("IATA")
    codigos = [c for c in sorted(set(vuelos["destino"]) | set(vuelos["origen"])) if c in db]
    inicio = (vuelos["salida_local"].min() - pd.Timedelta(days=3)).date().isoformat()
    fin = (vuelos["salida_local"].max() + pd.Timedelta(days=2)).date().isoformat()
    spc, nws = [], []
    for i, c in enumerate(codigos):
        s, n = riesgos_aeropuerto(c, db[c]["lat"], db[c]["lon"], inicio, fin)
        spc.append(s)
        nws.append(n)
        print(f"[{i + 1}/{len(codigos)}] {c}: SPC {len(s)}, NWS {len(n)}", flush=True)
        time.sleep(0.5)
    spc = pd.concat(spc, ignore_index=True)
    for col in ("utc_issue", "utc_expire", "utc_product_issue"):
        spc[col] = pd.to_datetime(spc[col])
    nws = pd.concat(nws, ignore_index=True)
    for col in ("issue", "expire"):
        nws[col] = pd.to_datetime(nws[col], utc=True).dt.tz_localize(None)
    spc.to_parquet(DATA / "processed" / "spc.parquet", index=False)
    nws[["aeropuerto", "phenomena", "significance", "name", "issue", "expire"]].to_parquet(
        DATA / "processed" / "nws.parquet", index=False)
    print(f"SPC: {len(spc)} panoramas; umbrales {spc['threshold'].value_counts().to_dict()}")
    print(f"NWS: {len(nws)} eventos; más comunes: {nws['name'].value_counts().head(12).to_dict()}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("paso", choices=["bts", "clima", "riesgos"])
    p.add_argument("--aeropuertos", default="IAH,DFW", help="orígenes separados por coma")
    p.add_argument("--desde", default="2024-09")
    p.add_argument("--hasta", default="2026-08")
    a = p.parse_args()
    {"bts": lambda: bts(a.aeropuertos.split(","), a.desde, a.hasta),
     "clima": clima, "riesgos": riesgos}[a.paso]()
