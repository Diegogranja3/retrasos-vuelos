"""Exporta una muestra de cada tabla (vuelos, clima, SPC, NWS) y el cruce final, a un solo Excel con una pestaña por tabla.

Las muestras son coherentes: las filas de clima, SPC y NWS son exactamente las que se pegaron a los vuelos de la muestra.
Uso: python -m scripts.muestras   ->  data/muestras/muestras.xlsx
"""
from pathlib import Path

import joblib
import pandas as pd
from openpyxl.styles import PatternFill

from src.features import ANTICIPACION, CAT, NUM

P = Path("data/processed")
SAL = Path("data/muestras")
SAL.mkdir(parents=True, exist_ok=True)

ds = pd.read_parquet(P / "dataset.parquet")
nws_cols = [c for c in ds if "_nws_" in c]
# 900 vuelos al azar + 50 con riesgo SPC + 50 con algún aviso NWS, para que se vean casos con riesgo.
muestra = pd.concat([ds.sample(900, random_state=42),
                     ds[ds["o_spc"] + ds["d_spc"] > 0].sample(50, random_state=42),
                     ds[ds[nws_cols].sum(axis=1) > 0].sample(50, random_state=42)])
muestra = muestra[~muestra.index.duplicated()].sort_values("salida_local").reset_index(drop=True)
muestra.insert(0, "id_vuelo", range(1, len(muestra) + 1))

# Vuelos tal como vienen del BTS (antes del cruce).
vuelos = muestra[["id_vuelo", "aerolinea", "operadora", "num_operadora", "origen", "destino",
                  "salida_local", "duracion_prog", "ArrDel15"]]

# Pares (aeropuerto, hora UTC) que usó cada vuelo, en origen y destino.
pares = pd.concat([muestra[["id_vuelo", "origen", "salida_utc", "hora_utc"]].rename(columns={"origen": "aeropuerto"})
                   .assign(papel="origen"),
                   muestra[["id_vuelo", "destino", "salida_utc", "hora_utc"]].rename(columns={"destino": "aeropuerto"})
                   .assign(papel="destino")])

clima = pd.read_parquet(P / "clima.parquet")
clima_m = (pares.merge(clima, left_on=["aeropuerto", "hora_utc"], right_on=["aeropuerto", "time"])
           .drop(columns=["time", "salida_utc"]).sort_values(["id_vuelo", "papel"], ascending=[True, False]))

# SPC: mismo filtro que src/features._spc (emitido >= 24 h antes y vigente a la salida).
spc = pd.read_parquet(P / "spc.parquet")
s = pares.merge(spc, on="aeropuerto")
spc_m = s[(s["utc_product_issue"] <= s["salida_utc"] - ANTICIPACION)
          & (s["utc_issue"] <= s["salida_utc"]) & (s["salida_utc"] < s["utc_expire"])]
spc_m = spc_m.drop(columns="hora_utc").sort_values(["id_vuelo", "utc_product_issue"])

# NWS: mismo filtro que src/features._nws (vigente a la salida y emitido >= 24 h antes).
nws = pd.read_parquet(P / "nws.parquet")
n = pares.merge(nws, on="aeropuerto")
nws_m = n[(n["issue"] + ANTICIPACION <= n["salida_utc"]) & (n["salida_utc"] < n["expire"])]
nws_m = nws_m.drop(columns="hora_utc").sort_values("id_vuelo")

# Lo que de verdad recibe el modelo: las mismas variables (CAT + NUM) que arma el pipeline entrenado,
# antes del one-hot y el escalado. id_vuelo es solo referencia y ArrDel15 es la respuesta, no entrada.
pipe = joblib.load("models/modelo.joblib")["modelo"].calibrated_classifiers_[0].estimator.estimator
entrada = pipe[:2].transform(muestra)[["id_vuelo", *CAT, *NUM, "ArrDel15"]]

tablas = {"1_vuelos": vuelos, "2_clima": clima_m, "3_spc": spc_m, "4_nws": nws_m, "5_cruce": muestra, "6_entrada_modelo": entrada}
with pd.ExcelWriter(SAL / "muestras.xlsx", engine="openpyxl") as xl:
    for nombre, t in tablas.items():
        t.to_excel(xl, sheet_name=nombre, index=False)
        print(f"{nombre}: {len(t)} filas")
    # Renglones retrasados (ArrDel15 = 1) en amarillo, en el cruce y en la entrada del modelo.
    amarillo = PatternFill("solid", fgColor="FFFF00")
    for hoja in ("5_cruce", "6_entrada_modelo"):
        for i in muestra.index[muestra["ArrDel15"] == 1]:
            for celda in xl.sheets[hoja][i + 2]:  # +2: encabezado y filas de Excel desde 1
                celda.fill = amarillo
