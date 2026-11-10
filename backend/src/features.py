"""Construcción de variables. Se usa IGUAL en entrenamiento y en inferencia (backend).

Entrada mínima por vuelo: salida_local (fecha-hora local programada, sin zona), origen, destino, aerolinea
(IATA comercializadora). En entrenamiento además: duracion_prog (CRSElapsedTime) y ArrDel15.

Regla de anticipación: todo dato externo debe haberse publicado >= 24 h antes de la salida programada
(clima *_previous_day1, panorama SPC Día 2, vigilancias/avisos del NWS).
"""
from functools import lru_cache

import airportsdata
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

ANTICIPACION = pd.Timedelta(hours=24)
VARS_CLIMA = ["temperature_2m", "precipitation", "snowfall", "cloud_cover",
              "wind_speed_10m", "wind_gusts_10m", "cape"]
RANGO_SPC = {"TSTM": 1, "MRGL": 2, "SLGT": 3, "ENH": 4, "MDT": 5, "HIGH": 6}
# Fenómenos VTEC del NWS agrupados por tipo de impacto; los demás cuentan como "otro".
GRUPOS_NWS = {"tropical": {"HU", "TR", "SS"},
              "invierno": {"WS", "BZ", "IS", "WW", "LE", "ZR", "EC", "XC", "WC", "FZ", "HZ"},
              "inundacion": {"FA", "FF", "FL", "CF"},
              "tormenta": {"SV", "TO"}}

PROHIBIDAS = {"DepTime", "DepDelay", "DepDel15", "TaxiOut", "WheelsOff", "WheelsOn", "TaxiIn", "ArrTime",
              "ArrDelay", "AirTime", "ActualElapsedTime", "CarrierDelay", "WeatherDelay", "NASDelay",
              "SecurityDelay", "LateAircraftDelay", "Cancelled", "Diverted", "ArrDel15"}

CAT = ["origen", "aerolinea", "destino", "mes", "dia_semana", "hora", "o_weather_code", "d_weather_code"]
NUM = (["dia_mes", "distancia", "duracion"]
       + [f"{l}{v}" for l in ("o_", "d_") for v in VARS_CLIMA]
       + [f"{l}spc" for l in ("o_", "d_")]
       + [f"{l}nws_{g}" for l in ("o_", "d_") for g in [*GRUPOS_NWS, "otro"]])
assert not PROHIBIDAS & set(CAT + NUM)


@lru_cache(maxsize=1)
def aeropuertos():
    return airportsdata.load("IATA")


def redondear_hora(t):
    """Hora más cercana con :30 hacia arriba (dt.round('h') redondea al par y desalinea local vs UTC)."""
    return (t + pd.Timedelta(minutes=30)).dt.floor("h")


def salida_utc(df):
    """salida_local (hora del aeropuerto de origen) -> UTC sin zona, con la zona IANA de cada origen."""
    utc = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    for origen, idx in df.groupby("origen").groups.items():
        tz = aeropuertos()[origen]["tz"]
        utc[idx] = (df.loc[idx, "salida_local"].dt.tz_localize(tz, nonexistent="shift_forward", ambiguous=False)
                    .dt.tz_convert("UTC").dt.tz_localize(None))
    return utc


def _spc(df, spc, col):
    """Riesgo SPC Día 2 vigente a la salida, del último panorama emitido >= 24 h antes (0 = sin riesgo)."""
    s = spc.assign(dia=spc["utc_issue"].dt.normalize(), rango=spc["threshold"].map(RANGO_SPC))
    v = df[[col, "salida_utc"]].assign(dia=(df["salida_utc"] - pd.Timedelta(hours=12)).dt.normalize(),
                                       fila=np.arange(len(df)))
    m = v.merge(s, left_on=[col, "dia"], right_on=["aeropuerto", "dia"])
    m = m[(m["utc_product_issue"] <= m["salida_utc"] - ANTICIPACION)
          & (m["utc_issue"] <= m["salida_utc"]) & (m["salida_utc"] < m["utc_expire"])]
    ultimo = m.sort_values("utc_product_issue").groupby("fila")["rango"].last()
    return ultimo.reindex(np.arange(len(df))).fillna(0).to_numpy()


def _nws(df, nws, col):
    """Por grupo: cuántos avisos/vigilancias, emitidos >= 24 h antes, siguen vigentes a la salida."""
    # Un evento cuenta para salidas en [issue + 24 h, expire); los más cortos que eso nunca cuentan.
    e = nws.assign(desde=nws["issue"] + ANTICIPACION)
    e = e[e["desde"] < e["expire"]]
    grupo = {f: g for g, fs in GRUPOS_NWS.items() for f in fs}
    e = e.assign(grupo=e["phenomena"].map(grupo).fillna("otro"))
    v = df[[col, "salida_utc"]].assign(fila=np.arange(len(df)))
    m = v.merge(e, left_on=col, right_on="aeropuerto")
    m = m[(m["desde"] <= m["salida_utc"]) & (m["salida_utc"] < m["expire"])]
    conteo = m.groupby(["fila", "grupo"]).size().unstack(fill_value=0)
    return {g: conteo.get(g, pd.Series(dtype=int)).reindex(np.arange(len(df))).fillna(0).to_numpy()
            for g in [*GRUPOS_NWS, "otro"]}


def agregar_contexto(df, clima, spc, nws):
    """Une clima pronosticado y riesgos (origen y destino) a cada vuelo. No cambia el número de filas."""
    df = df.reset_index(drop=True).copy()
    df["salida_utc"] = salida_utc(df)
    df["hora_utc"] = redondear_hora(df["salida_utc"])
    n = len(df)
    cols = ["time", "aeropuerto", *VARS_CLIMA, "weather_code"]
    for l, col in [("o_", "origen"), ("d_", "destino")]:
        c = clima[cols].rename(columns={v: l + v for v in [*VARS_CLIMA, "weather_code"]})
        df = df.merge(c.rename(columns={"time": "hora_utc", "aeropuerto": col}), on=["hora_utc", col], how="left")
        df[f"{l}spc"] = _spc(df, spc, col)
        for g, valores in _nws(df, nws, col).items():
            df[f"{l}nws_{g}"] = valores
    assert len(df) == n, "el cruce duplicó filas"
    return df


def construir_variables(X):
    """Variables de calendario y distancia de gran círculo (paso sin estado del pipeline)."""
    X = X.copy()
    t = pd.to_datetime(X["salida_local"])
    X["mes"], X["dia_mes"], X["dia_semana"], X["hora"] = t.dt.month, t.dt.day, t.dt.dayofweek, t.dt.hour
    db = aeropuertos()
    lat1, lon1, lat2, lon2 = (np.radians(X[c].map(lambda a, k=k: db[a][k]).astype(float))
                              for c, k in [("origen", "lat"), ("origen", "lon"), ("destino", "lat"), ("destino", "lon")])
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    X["distancia"] = 2 * 3958.8 * np.arcsin(np.sqrt(a))  # millas, como Distance de BTS
    for c in ("o_weather_code", "d_weather_code"):
        X[c] = X[c].astype("Int64").astype(str)
    return X


class DuracionMediana(BaseEstimator, TransformerMixin):
    """Duración programada estimada: mediana del entrenamiento por (aerolínea, origen, destino),
    luego por (origen, destino) y luego global. La API en vivo no trae duración, por eso se estima siempre."""

    def fit(self, X, y=None):
        d = X["duracion_prog"]
        self.por_ruta_aerolinea_ = d.groupby([X["aerolinea"], X["origen"], X["destino"]]).median()
        self.por_ruta_ = d.groupby([X["origen"], X["destino"]]).median()
        self.global_ = float(d.median())
        return self

    def transform(self, X):
        X = X.copy()
        k3 = pd.MultiIndex.from_frame(X[["aerolinea", "origen", "destino"]])
        k2 = pd.MultiIndex.from_frame(X[["origen", "destino"]])
        X["duracion"] = (self.por_ruta_aerolinea_.reindex(k3).to_numpy())
        falta = np.isnan(X["duracion"].to_numpy())
        X.loc[falta, "duracion"] = self.por_ruta_.reindex(k2[falta]).to_numpy()
        return X.fillna({"duracion": self.global_})
