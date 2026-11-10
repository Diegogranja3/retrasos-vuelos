"""Fase 0, prueba 3: cruce de vuelos del DOT/BTS con clima pronosticado (Open-Meteo Previous Runs API).

- Descarga un mes de On-Time Performance (PREZIP de TranStats) y filtra salidas de un aeropuerto.
- Convierte la hora programada (local del origen) a UTC con la zona horaria IANA del aeropuerto.
- Une el clima pronosticado 24 h antes (variables *_previous_day1) en origen y destino a la hora de salida.
- Verifica pérdidas de filas, el cambio de horario y repite el cruce por hora local como control.

Uso: python scripts/check_weather_join.py --anio 2026 --mes 3 --aeropuerto IAH
Salida: data/interim/{aeropuerto}_{anio}_{mes:02d}.parquet
"""
import argparse
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import airportsdata
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
DATA = RAIZ.parent / "data"  # data/ vive fuera de backend/
URL_BTS = "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{a}_{m}.zip"
URL_CLIMA = "https://previous-runs-api.open-meteo.com/v1/forecast"
# Pronóstico emitido 24 h antes de la hora válida: disponible "con anticipación", igual que en la demo.
# (visibility se probó y viene vacía en esta API; por eso no está.)
VARS_CLIMA = ["temperature_2m", "precipitation", "snowfall", "cloud_cover",
              "wind_speed_10m", "wind_gusts_10m", "weather_code"]
SUFIJO = "_previous_day1"

# Solo variables conocidas antes del despegue + etiqueta y columnas para filtrar.
COLS = ["Month", "DayofMonth", "DayOfWeek", "FlightDate", "Reporting_Airline", "Origin", "Dest",
        "CRSDepTime", "CRSElapsedTime", "Distance", "ArrDel15", "Cancelled", "Diverted"]


def leer_bts(anio, mes, aeropuerto):
    zpath = DATA / "raw" / "bts" / f"ontime_{anio}_{mes}.zip"
    if not zpath.exists():
        zpath.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL_BTS.format(a=anio, m=mes), zpath)
    with zipfile.ZipFile(zpath) as z:
        csv = next(n for n in z.namelist() if n.endswith(".csv"))
        df = pd.read_csv(z.open(csv), usecols=COLS, dtype={"CRSDepTime": "int32"})
    return df[df["Origin"] == aeropuerto].reset_index(drop=True)


def hora_salida(df, tz_por_aeropuerto):
    """Fecha+hora programada local -> instante UTC. CRSDepTime viene como hhmm (2400 = medianoche siguiente)."""
    hhmm = df["CRSDepTime"]
    local = (pd.to_datetime(df["FlightDate"]) + pd.to_timedelta(hhmm // 100, "h") + pd.to_timedelta(hhmm % 100, "min"))
    utc = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    for origen, idx in df.groupby("Origin").groups.items():
        utc[idx] = (local[idx].dt.tz_localize(tz_por_aeropuerto[origen], nonexistent="NaT", ambiguous="NaT")
                    .dt.tz_convert("UTC"))
    return local, utc


def redondear_hora(t):
    """Hora más cercana, con :30 hacia arriba (dt.round('h') redondea al par y desalinea local vs UTC)."""
    return (t + pd.Timedelta(minutes=30)).dt.floor("h")


def descargar_clima(aeropuertos, coords, inicio, fin, zona="GMT"):
    """Clima horario por aeropuerto. Open-Meteo acepta varias coordenadas por llamada (lista de respuestas)."""
    filas = []
    for i in range(0, len(aeropuertos), 50):
        lote = aeropuertos[i:i + 50]
        q = {"latitude": ",".join(str(coords[a][0]) for a in lote),
             "longitude": ",".join(str(coords[a][1]) for a in lote),
             "hourly": ",".join(v + SUFIJO for v in VARS_CLIMA),
             "start_date": inicio, "end_date": fin, "timezone": zona}
        with urllib.request.urlopen(f"{URL_CLIMA}?{urllib.parse.urlencode(q)}", timeout=120) as r:
            resp = json.load(r)
        for a, d in zip(lote, resp if isinstance(resp, list) else [resp]):
            h = pd.DataFrame(d["hourly"])
            h.columns = [c.removesuffix(SUFIJO) for c in h.columns]
            h["aeropuerto"] = a
            filas.append(h)
    clima = pd.concat(filas, ignore_index=True)
    clima["time"] = pd.to_datetime(clima["time"])
    return clima


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--anio", type=int, default=2026)
    p.add_argument("--mes", type=int, default=3)
    p.add_argument("--aeropuerto", default="IAH")
    p.add_argument("--semana", default="2026-03-05", help="inicio de la semana a reportar aparte (7 días)")
    a = p.parse_args()

    df = leer_bts(a.anio, a.mes, a.aeropuerto)
    n0 = len(df)
    df = df[(df["Cancelled"] == 0) & (df["Diverted"] == 0)].drop(columns=["Cancelled", "Diverted"])
    print(f"Salidas de {a.aeropuerto} en {a.anio}-{a.mes:02d}: {n0}; tras quitar cancelados/desviados: {len(df)}")
    print(f"ArrDel15 nulo tras filtrar: {df['ArrDel15'].isna().sum()}")

    # Coordenadas y zona horaria IANA de cada aeropuerto.
    db = airportsdata.load("IATA")
    aeropuertos = sorted(set(df["Origin"]) | set(df["Dest"]))
    sin_info = [x for x in aeropuertos if x not in db]
    print(f"Aeropuertos involucrados: {len(aeropuertos)}; sin coordenadas/zona: {sin_info or 'ninguno'}")
    df = df[~df["Dest"].isin(sin_info)]
    coords = {x: (db[x]["lat"], db[x]["lon"]) for x in aeropuertos if x in db}
    tz = {x: db[x]["tz"] for x in coords}

    df["dep_local"], df["dep_utc"] = hora_salida(df, tz)
    invalidas = df[df["dep_utc"].isna()]
    print(f"Horas locales inexistentes/ambiguas por cambio de horario: {len(invalidas)}")
    if len(invalidas):
        print(invalidas[["FlightDate", "CRSDepTime", "Reporting_Airline", "Dest"]].to_string())
    df = df.dropna(subset=["dep_utc"])
    df["hora_utc"] = redondear_hora(df["dep_utc"]).dt.tz_localize(None)

    # Clima en UTC para todos los aeropuertos (+1 día de margen por el desfase horario).
    ini = (df["hora_utc"].min() - pd.Timedelta(days=1)).date().isoformat()
    fin = (df["hora_utc"].max() + pd.Timedelta(days=1)).date().isoformat()
    clima = descargar_clima(list(coords), coords, ini, fin)
    nulos = clima[VARS_CLIMA].isna().mean().round(4)
    print(f"\nClima descargado: {len(clima)} filas ({clima['aeropuerto'].nunique()} aeropuertos, {ini} a {fin})")
    print("Fracción de nulos por variable:\n" + nulos.to_string())

    n_antes = len(df)
    for lado, col in [("o_", "Origin"), ("d_", "Dest")]:
        c = clima.rename(columns={v: lado + v for v in VARS_CLIMA}).rename(columns={"time": "hora_utc", "aeropuerto": col})
        df = df.merge(c, on=["hora_utc", col], how="left")
    assert len(df) == n_antes, "el merge duplicó filas"
    cols_clima = [l + v for l in ("o_", "d_") for v in VARS_CLIMA]
    sin_clima = df[cols_clima].isna().any(axis=1)
    print(f"\nFilas tras el cruce: {len(df)} (antes {n_antes}); filas con algún dato de clima nulo: {sin_clima.sum()}")

    # Control 1: el desfase local->UTC cambia el 8 de marzo (CST -6 -> CDT -5).
    desfase = (df["dep_local"] - df["dep_utc"].dt.tz_localize(None)).dt.total_seconds() / 3600
    print("\nDesfase local-UTC (h) por día alrededor del cambio de horario:")
    print(df.assign(desfase=desfase).groupby(df["dep_local"].dt.date)["desfase"].agg(["min", "max"])
          .loc[lambda t: [str(d) in {"2026-03-07", "2026-03-08", "2026-03-09"} for d in t.index]].to_string())

    # Control 2: ¿sirve pedir el clima directamente en hora local? Open-Meteo aplica UN desfase fijo
    # (utc_offset_seconds) a todo el rango, sin cambio de horario. Por eso el cruce se hace en UTC.
    loc = descargar_clima([a.aeropuerto], coords, ini, fin, zona=tz[a.aeropuerto])
    df["hora_local"] = redondear_hora(df["dep_local"])
    chk = df.merge(loc[["time", "temperature_2m"]].rename(columns={"time": "hora_local", "temperature_2m": "t_local"}),
                   on="hora_local", how="left")
    distintos = (chk["t_local"] != chk["o_temperature_2m"]) & chk["t_local"].notna()
    horario = (desfase.round() == -5).map({True: "verano (CDT)", False: "invierno (CST)"})
    print("\nControl: clima pedido en 'hora local' a Open-Meteo vs cruce vía UTC (temperatura en origen)")
    print(pd.DataFrame({"vuelos": horario.value_counts(), "diferencias": horario[distintos.values].value_counts()})
          .fillna(0).astype(int).to_string())

    semana = df[(df["dep_local"] >= a.semana) & (df["dep_local"] < pd.Timestamp(a.semana) + pd.Timedelta(days=7))]
    print(f"\nSemana {a.semana} (+7 días): {len(semana)} vuelos, con clima completo: {(~semana[cols_clima].isna().any(axis=1)).sum()}")
    print("\nMuestra para revisión manual:")
    print(df.sample(5, random_state=42)[["FlightDate", "CRSDepTime", "dep_local", "hora_utc", "Dest",
                                         "o_temperature_2m", "o_precipitation", "d_temperature_2m"]].to_string())

    out = DATA / "interim" / f"{a.aeropuerto}_{a.anio}_{a.mes:02d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["hora_local"]).to_parquet(out, index=False)
    print(f"\nGuardado: {out}  | tasa de retraso (ArrDel15=1): {df['ArrDel15'].mean():.3f}")


if __name__ == "__main__":
    main()
