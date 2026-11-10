"""Escenarios fijos del simulador para comparar modelos (antes/después de agregar ORD).

Uso: python -m scripts.escenarios [ruta_modelo]   (por omisión models/modelo.joblib)
"""
import os
import sys

from fastapi.testclient import TestClient

if len(sys.argv) > 1:
    os.environ["MODELO_PATH"] = sys.argv[1]
from api.main import app  # noqa: E402  (lee MODELO_PATH al cargar el modelo)

DESPEJADO = {}
NIEVE = {"temperatura": -4, "precipitacion": 2, "nieve": 3, "nubosidad": 100, "viento": 25, "rafagas": 50,
         "codigo": 73, "avisos": {"invierno": 1}}
TORMENTA = {"temperatura": 28, "precipitacion": 15, "nubosidad": 100, "viento": 30, "rafagas": 75, "cape": 3000,
            "codigo": 95, "spc": 3, "avisos": {"tormenta": 1}}
ESCENARIOS = [
    ("IAH→DEN ene 17:00 despejado", "IAH", "DEN", "2026-01-15T17:00", DESPEJADO, DESPEJADO),
    ("IAH→DEN ene 17:00 nieve en origen", "IAH", "DEN", "2026-01-15T17:00", NIEVE, DESPEJADO),
    ("IAH→DEN ene 17:00 nieve en destino", "IAH", "DEN", "2026-01-15T17:00", DESPEJADO, NIEVE),
    ("IAH→ORD jul 17:00 tormenta en destino", "IAH", "ORD", "2026-07-15T17:00", DESPEJADO, TORMENTA),
    ("ORD→DEN ene 17:00 despejado", "ORD", "DEN", "2026-01-15T17:00", DESPEJADO, DESPEJADO),
    ("ORD→DEN ene 17:00 nieve en origen", "ORD", "DEN", "2026-01-15T17:00", NIEVE, DESPEJADO),
]

c = TestClient(app)
for nombre, o, d, t, co, cd in ESCENARIOS:
    r = c.post("/api/simular", json={"origen": o, "destino": d, "aerolinea": "UA", "salida_local": t,
                                     "clima_origen": co, "clima_destino": cd})
    print(f"{nombre:40s} {r.json()['probabilidad']:.1%}" if r.status_code == 200 else f"{nombre:40s} — ({r.json()['detail']})")
