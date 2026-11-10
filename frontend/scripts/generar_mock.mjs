// Convierte una respuesta de AeroDataBox (salidas de IAH) al contrato de
// GET /api/vuelos y escribe web/public/mock/vuelos.json.
//
// Uso:  node scripts/generar_mock.mjs [ruta/al/archivo.json]
// Sin argumento toma el .json más reciente de ../data/raw/api/.
//
// Las probabilidades y el clima son INVENTADOS (pseudoaleatorios con semilla
// fija) para poder probar la interfaz; no son predicciones reales.
// No se copia la respuesta cruda: solo se escriben los campos del contrato.

import { readFileSync, writeFileSync, readdirSync, statSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const aqui = dirname(fileURLToPath(import.meta.url))
const raizWeb = resolve(aqui, '..')
const dirCrudos = resolve(raizWeb, '..', 'data', 'raw', 'api')
const salida = join(raizWeb, 'public', 'mock', 'vuelos.json')

const UMBRAL = 0.32
const SEMILLA = 20261029

// Generador pseudoaleatorio determinista (mulberry32).
function crearAzar(semilla) {
  let a = semilla >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// Hash simple de texto -> entero, para que el clima de cada aeropuerto sea estable.
function hashTexto(texto) {
  let h = 2166136261
  for (const c of texto) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return h >>> 0
}

const redondear = (x, d = 1) => Math.round(x * 10 ** d) / 10 ** d

function elegirArchivo() {
  const arg = process.argv[2]
  if (arg) return resolve(arg)
  const archivos = readdirSync(dirCrudos)
    .filter((f) => f.endsWith('.json'))
    .map((f) => join(dirCrudos, f))
    .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
  if (!archivos.length) throw new Error(`No hay archivos .json en ${dirCrudos}`)
  return archivos[0]
}

// Clima plausible de finales de octubre, estable por aeropuerto.
function climaDe(iata, latitud) {
  const azar = crearAzar(hashTexto(iata ?? 'XXX') ^ SEMILLA)
  const lat = Math.abs(latitud ?? 30)
  const temperatura = redondear(30 - (lat - 15) * 0.6 + (azar() - 0.5) * 10)
  const llueve = azar() < 0.3
  const precipitacion = llueve ? redondear(azar() * 6) : 0
  const rafagas = redondear(15 + azar() * 40)
  const cape = azar() < 0.25 ? Math.round(azar() * 1500) : Math.round(azar() * 150)
  const spc = cape > 1000 ? 'SLGT' : cape > 500 ? 'MRGL' : cape > 300 ? 'TSTM' : null
  const avisos_nws = []
  if (rafagas > 48) avisos_nws.push('Wind Advisory')
  if (precipitacion > 4.5) avisos_nws.push('Flood Watch')
  return { temperatura, precipitacion, rafagas, cape, spc, avisos_nws }
}

function main() {
  const archivo = elegirArchivo()
  const crudo = JSON.parse(readFileSync(archivo, 'utf8'))
  const salidas = crudo.departures ?? []
  const azar = crearAzar(SEMILLA)

  // Clima del origen: un poco de tormenta para que haya variedad.
  const climaOrigen = {
    temperatura: 24.6, precipitacion: 1.8, rafagas: 41.3, cape: 1250, spc: 'SLGT',
    avisos_nws: ['Severe Thunderstorm Watch'],
  }

  const usados = new Set()
  const vuelos = []
  for (const s of salidas) {
    const mov = s.movement ?? {}
    const ap = mov.airport ?? {}
    const hora = mov.scheduledTime ?? {}
    if (!hora.local || !hora.utc) continue

    // "2026-10-29 06:00-05:00" -> "2026-10-29T06:00"; "2026-10-29 11:00Z" -> "2026-10-29T11:00Z"
    const salida_local = hora.local.slice(0, 16).replace(' ', 'T')
    const salida_utc = hora.utc.replace(' ', 'T')
    const fecha = salida_local.slice(0, 10)
    const numero = s.number ?? 'Desconocido'
    let id = `${numero.replace(/\s+/g, '')}-${fecha}`
    for (let n = 2; usados.has(id); n++) id = `${numero.replace(/\s+/g, '')}-${fecha}-${n}`
    usados.add(id)

    const lat = ap.location?.lat ?? null
    const lon = ap.location?.lon ?? null

    let excluido = null
    if (s.codeshareStatus === 'IsCodeshared') excluido = 'codeshare duplicado'
    else if (ap.countryCode !== 'us') excluido = 'vuelo internacional'
    else if (lat == null || lon == null) excluido = 'destino sin coordenadas'

    const climaDestino = climaDe(ap.iata, lat)
    // Siempre se consume el azar para que el resultado no dependa de las exclusiones.
    const base = azar()
    let probabilidad = null
    let prediccion = null
    if (!excluido) {
      let p = 0.04 + 0.5 * base ** 2
      if (climaDestino.spc) p += 0.08
      if (climaDestino.rafagas > 45) p += 0.05
      const h = Number(salida_local.slice(11, 13))
      p += Math.max(0, h - 12) * 0.012 // los retrasos se acumulan en la tarde
      probabilidad = redondear(Math.min(p, 0.95), 2)
      prediccion = probabilidad >= UMBRAL ? 'retrasado' : 'a_tiempo'
    }

    vuelos.push({
      id,
      numero,
      aerolinea: s.airline?.iata ?? null,
      aerolinea_nombre: s.airline?.name ?? null,
      destino: { iata: ap.iata ?? null, nombre: ap.name ?? null, lat, lon },
      salida_local,
      salida_utc,
      estado: s.status ?? null,
      probabilidad,
      prediccion,
      excluido,
      contexto: { origen: climaOrigen, destino: climaDestino },
      real: { llegada_retraso_min: null, retrasado: null },
    })
  }

  vuelos.sort((a, b) => a.salida_local.localeCompare(b.salida_local))

  const respuesta = {
    aeropuerto: {
      iata: 'IAH', nombre: 'Houston George Bush', lat: 29.9844, lon: -95.3414, tz: 'America/Chicago',
    },
    generado: new Date(statSync(archivo).mtime).toISOString().replace(/\.\d+Z$/, 'Z'),
    fuente: 'mock',
    modelo: { nombre: 'random_forest', umbral: UMBRAL, provisional: true },
    vuelos,
  }

  mkdirSync(dirname(salida), { recursive: true })
  writeFileSync(salida, JSON.stringify(respuesta) + '\n', 'utf8')
  const excluidos = vuelos.filter((v) => v.excluido).length
  console.log(`Escrito ${salida}: ${vuelos.length} vuelos (${excluidos} excluidos) desde ${archivo}`)
}

main()
