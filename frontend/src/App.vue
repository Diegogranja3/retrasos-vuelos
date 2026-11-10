<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import MapaVuelos from './components/MapaVuelos.vue'
import TarjetaFactores from './components/TarjetaFactores.vue'
import SimuladorVuelo from './components/SimuladorVuelo.vue'
import AsistenteIA from './components/AsistenteIA.vue'
import { explicarVuelo, obtenerVuelos } from './api.js'
import { NIVELES, hora, nivelDe, porcentaje } from './util.js'

// Orígenes con los que se entrenó el modelo.
const AEROPUERTOS = {
  IAH: { iata: 'IAH', nombre: 'Houston George Bush', lat: 29.9844, lon: -95.3414, tz: 'America/Chicago' },
  DFW: { iata: 'DFW', nombre: 'Dallas/Fort Worth', lat: 32.8968, lon: -97.038, tz: 'America/Chicago' },
  ORD: { iata: 'ORD', nombre: 'Chicago O\'Hare', lat: 41.9786, lon: -87.9048, tz: 'America/Chicago' },
}
const codigoAeropuerto = ref('IAH')

/**
 * Próximas 12 h desde la hora en punto actual del aeropuerto (horas locales, AAAA-MM-DDTHH:MM).
 * Empezar en la hora en punto hace que clics dentro de la misma hora reutilicen la caché del backend.
 */
function proximas12h() {
  const p = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: AEROPUERTOS[codigoAeropuerto.value].tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date()).map(({ type, value }) => [type, value]),
  )
  // ponytail: se suma en "reloj local"; el día de cambio de horario la ventana queda 1 h corta o larga.
  const inicio = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour)
  const fmt = (ms) => new Date(ms).toISOString().slice(0, 16)
  return { desde: fmt(inicio), hasta: fmt(inicio + (11 * 60 + 59) * 6e4) }
}

// Resultado de la consulta
const cargando = ref(false)
const error = ref(null)
const datos = ref(null)
const esMock = ref(false)
const motivoMock = ref(null)
const consultaActual = ref(null)
const idSeleccionado = ref(null)
const pestana = ref('vuelos') // 'vuelos' | 'simular'
const vueloSimulado = ref(null)
let simulaciones = 0

// ---------- Notificaciones (esquina inferior derecha) ----------
// tipo: 'cargando' | 'ok' | 'aviso' | 'error'. Las de carga se quitan a mano; las demás se van solas.
const avisos = ref([])
const DURACION = { ok: 4000, aviso: 6000, error: 9000 }
let idAviso = 0
function avisar(tipo, titulo, detalle = null) {
  const id = ++idAviso
  avisos.value.push({ id, tipo, titulo, detalle })
  if (DURACION[tipo]) setTimeout(() => quitarAviso(id), DURACION[tipo])
  return id
}
const quitarAviso = (id) => (avisos.value = avisos.value.filter((a) => a.id !== id))

const OK_VUELOS = 'La información de los vuelos fue consultada exitosamente.'
const OK_CLIMA = 'La información del clima fue consultada exitosamente.'
const FALLO_VUELOS = 'Fallo en la consulta de los vuelos.'
const FALLO_CLIMA = 'Fallo en la consulta del clima.'
const GUARDADAS = 'Se muestran las últimas predicciones guardadas.'

/** Una notificación por consulta (vuelos y clima) según lo que respondió el backend. */
function notificarResultado(r) {
  const d = r.datos
  if (r.esMock) {
    avisar('error', FALLO_VUELOS, `${r.motivoMock}. Se muestran datos de prueba.`)
    avisar('error', FALLO_CLIMA, 'No se consultó porque no hubo vuelos en vivo.')
  } else if (d.fallo === 'vuelos') {
    avisar('error', FALLO_VUELOS, `${d.fallo_detalle}. ${GUARDADAS}`)
  } else if (d.fallo === 'clima') {
    avisar('ok', OK_VUELOS)
    avisar('error', FALLO_CLIMA, `${d.fallo_detalle}. ${GUARDADAS}`)
  } else {
    avisar('ok', OK_VUELOS)
    if (d.clima === 'respaldo') avisar('aviso', 'Clima consultado con el pronóstico de respaldo.', 'Open-Meteo Previous Runs no respondió; se usó el pronóstico actual.')
    else if (d.clima === 'ok') avisar('ok', OK_CLIMA)
  }
}

async function consultar() {
  const consulta = { aeropuerto: codigoAeropuerto.value, ...proximas12h() }
  cargando.value = true
  error.value = null
  const idCarga = avisar('cargando', 'Consultando información…', `Vuelos y clima de ${consulta.aeropuerto}`)
  try {
    const r = await obtenerVuelos(consulta)
    datos.value = r.datos
    esMock.value = r.esMock
    motivoMock.value = r.motivoMock
    consultaActual.value = consulta
    // Arranca mostrando el vuelo con mayor probabilidad de retraso.
    idSeleccionado.value = ranking.value[0]?.id ?? null
    notificarResultado(r)
  } catch (e) {
    error.value = e.message || 'Error desconocido al consultar los vuelos.'
    if (e.fallo === 'clima') {
      avisar('ok', OK_VUELOS)
      avisar('error', FALLO_CLIMA, e.message)
    } else {
      avisar('error', FALLO_VUELOS, error.value)
    }
  } finally {
    quitarAviso(idCarga)
    cargando.value = false
  }
}

function cambiarAeropuerto(codigo) {
  if (codigo === codigoAeropuerto.value || cargando.value) return
  codigoAeropuerto.value = codigo
  idSeleccionado.value = null
  vueloSimulado.value = null
  consultar()
}

onMounted(consultar)

// Mientras carga otro aeropuerto se usa el elegido, no el de la respuesta anterior.
const aeropuerto = computed(() =>
  datos.value?.aeropuerto?.iata === codigoAeropuerto.value ? datos.value.aeropuerto : AEROPUERTOS[codigoAeropuerto.value],
)

// Vuelos con predicción, de mayor a menor probabilidad de retraso (los excluidos no se muestran).
const ranking = computed(() =>
  (datos.value?.vuelos ?? [])
    .filter((v) => !v.excluido && v.probabilidad != null && v.destino?.lat != null)
    .map((v) => ({ ...v, nivel: nivelDe(v.probabilidad) }))
    .sort((a, b) => b.probabilidad - a.probabilidad || a.salida_local.localeCompare(b.salida_local)),
)

const vueloSeleccionado = computed(() =>
  vueloSimulado.value?.id === idSeleccionado.value
    ? vueloSimulado.value
    : ranking.value.find((v) => v.id === idSeleccionado.value) ?? null,
)

// Resultado del simulador: se anima igual que un vuelo real. Cada simulación lleva su propio id
// para que repetir los mismos datos vuelva a lanzar la animación.
function alSimular(v) {
  simulaciones += 1
  vueloSimulado.value = { ...v, id: `${v.id}-${simulaciones}`, nivel: nivelDe(v.probabilidad) }
  idSeleccionado.value = vueloSimulado.value.id
}

// La tarjeta de factores entra unos segundos después de elegir el vuelo, cuando el avión ya va en camino.
const RETRASO_TARJETA_MS = 2500
const tarjetaVisible = ref(false)
let temporizador = null
watch(idSeleccionado, (id) => {
  clearTimeout(temporizador)
  tarjetaVisible.value = false
  // La explicación por IA tarda unos segundos: se pide ya, mientras despega el avión.
  if (vueloSeleccionado.value) explicarVuelo(aeropuerto.value.iata, vueloSeleccionado.value).catch(() => {})
  if (id) temporizador = setTimeout(() => (tarjetaVisible.value = true), RETRASO_TARJETA_MS)
})

const generado = computed(() => {
  const g = datos.value?.generado
  if (!g) return null
  const f = new Date(g)
  return isNaN(f) ? g : f.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
})
const FUENTES = { api: 'API en vivo', cache: 'caché', mock: 'datos de prueba' }
</script>

<template>
  <div class="app">
    <main class="zona-mapa">
      <MapaVuelos
        :aeropuerto="aeropuerto"
        :vuelos="ranking"
        :vuelo="vueloSeleccionado"
        @seleccionar-vuelo="(id) => (idSeleccionado = id)"
      />

      <header class="titulo-app vidrio">
        <h1>Predicción de retrasos de vuelos</h1>
        <p>Aprendizaje automático con datos oficiales de EE. UU. y clima de Open-Meteo</p>
      </header>

      <aside class="ranking vidrio" aria-labelledby="titulo-ranking">
        <header class="ranking-cabecera">
          <h2 id="titulo-ranking">{{ pestana === 'vuelos' ? 'Vuelos con más riesgo de retraso' : 'Simular un vuelo' }}</h2>
          <div class="pestanas" role="tablist" aria-label="Sección">
            <button type="button" role="tab" :aria-selected="pestana === 'vuelos'" @click="pestana = 'vuelos'">Vuelos</button>
            <button type="button" role="tab" :aria-selected="pestana === 'simular'" @click="pestana = 'simular'">Simular</button>
          </div>
          <div class="ranking-controles">
            <div class="segmentado" role="group" aria-label="Aeropuerto de salida">
              <button
                v-for="a in AEROPUERTOS"
                :key="a.iata"
                type="button"
                :aria-pressed="a.iata === codigoAeropuerto"
                :disabled="cargando"
                :title="a.nombre"
                @click="cambiarAeropuerto(a.iata)"
              >
                {{ a.iata }}
              </button>
            </div>
            <button type="button" class="boton-icono" :disabled="cargando" aria-label="Actualizar vuelos" title="Actualizar vuelos" @click="consultar">
              ↻
            </button>
          </div>
          <p class="sub">
            Salidas de {{ aeropuerto.iata }}
            <template v-if="consultaActual"> · {{ hora(consultaActual.desde) }}–{{ hora(consultaActual.hasta) }}</template>
            · {{ ranking.length }} vuelos
          </p>
        </header>

        <SimuladorVuelo v-show="pestana === 'simular'" :origen="codigoAeropuerto" @resultado="alSimular" />

        <template v-if="pestana === 'vuelos'">
          <div v-if="error" class="aviso-error" role="alert"><strong>Error:</strong> {{ error }}</div>
          <div v-else-if="cargando && !ranking.length" class="esqueleto" role="status">
            <p class="esqueleto-texto">Consultando vuelos y clima… <span>(la primera vez tarda ~1 min)</span></p>
            <!-- ponytail: 6 filas fijas, solo decorativas -->
            <div v-for="n in 6" :key="n" class="esqueleto-fila" aria-hidden="true" :style="{ animationDelay: `${n * 0.08}s` }">
              <span class="hueso hueso-posicion"></span>
              <span class="esqueleto-info">
                <span class="hueso hueso-titulo"></span>
                <span class="hueso hueso-sub"></span>
              </span>
              <span class="hueso hueso-insignia"></span>
            </div>
          </div>
          <p v-else-if="!ranking.length" class="vacio">No hay vuelos con predicción en las próximas 12 h.</p>

          <ol v-else class="lista-ranking" :class="{ actualizando: cargando }" :aria-busy="cargando">
            <li v-for="(v, i) in ranking" :key="v.id">
              <button
                type="button"
                class="fila-ranking"
                :aria-current="v.id === idSeleccionado ? 'true' : undefined"
                @click="idSeleccionado = v.id"
              >
                <span class="posicion">{{ i + 1 }}</span>
                <span class="vuelo-info">
                  <strong>{{ v.numero }} → {{ v.destino.iata }}</strong>
                  <span class="vuelo-destino">{{ hora(v.salida_local) }} · {{ v.destino.nombre }}</span>
                </span>
                <span class="insignia" :style="{ background: NIVELES[v.nivel].color, color: NIVELES[v.nivel].tinta }" :title="NIVELES[v.nivel].texto">
                  {{ porcentaje(v.probabilidad) }}
                  <span class="solo-lector">{{ NIVELES[v.nivel].texto }}</span>
                </span>
              </button>
            </li>
          </ol>
        </template>

        <p v-if="datos" class="pie">
          Modelo {{ datos.modelo?.nombre ?? '—' }} · {{ FUENTES[datos.fuente] ?? datos.fuente }}<span v-if="generado"> · {{ generado }}</span>
        </p>
      </aside>

      <Transition name="deslizar">
        <TarjetaFactores
          v-if="vueloSeleccionado && tarjetaVisible"
          :key="vueloSeleccionado.id"
          :vuelo="vueloSeleccionado"
          :origen="aeropuerto.iata"
          @cerrar="idSeleccionado = null"
        />
      </Transition>

      <Transition name="subir">
        <AsistenteIA
          v-if="vueloSeleccionado && tarjetaVisible"
          :key="vueloSeleccionado.id"
          :vuelo="vueloSeleccionado"
          :origen="aeropuerto.iata"
        />
      </Transition>

      <div v-if="esMock" class="distintivo-mock" role="status">
        <strong>DATOS DE PRUEBA — no son predicciones reales</strong>
        <span v-if="motivoMock">({{ motivoMock }})</span>
      </div>

      <TransitionGroup tag="div" name="aviso" class="avisos" aria-live="polite">
        <div v-for="a in avisos" :key="a.id" class="aviso vidrio" :class="`aviso-${a.tipo}`" :role="a.tipo === 'error' ? 'alert' : 'status'">
          <span v-if="a.tipo === 'cargando'" class="spinner" aria-hidden="true"></span>
          <span v-else class="aviso-icono" aria-hidden="true">{{ { ok: '✓', aviso: '!', error: '✕' }[a.tipo] }}</span>
          <span class="aviso-texto">
            <strong>{{ a.titulo }}</strong>
            <small v-if="a.detalle">{{ a.detalle }}</small>
          </span>
          <button v-if="a.tipo !== 'cargando'" type="button" class="aviso-cerrar" aria-label="Cerrar notificación" @click="quitarAviso(a.id)">×</button>
        </div>
      </TransitionGroup>
    </main>
  </div>
</template>
