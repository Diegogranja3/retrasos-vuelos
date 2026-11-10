<script setup>
// Simulador: arma a mano un vuelo (destino, aerolínea, hora y clima) y lo evalúa con el modelo.
// Pide exactamente las variables del modelo; el resto (calendario, distancia, duración) lo calcula el backend.
import { onMounted, reactive, ref } from 'vue'
import { obtenerOpciones, simularVuelo } from '../api.js'

const props = defineProps({ origen: { type: String, required: true } })
const emit = defineEmits(['resultado'])

// Condiciones típicas para llenar el clima con un clic (unidades de Open-Meteo, como en el entrenamiento).
const PRESETS = {
  despejado: { temperatura: 22, precipitacion: 0, nieve: 0, nubosidad: 10, viento: 10, rafagas: 20, cape: 100, codigo: 0, spc: 0, avisos: {} },
  lluvia: { temperatura: 18, precipitacion: 4, nieve: 0, nubosidad: 100, viento: 20, rafagas: 40, cape: 300, codigo: 63, spc: 0, avisos: {} },
  tormenta: { temperatura: 28, precipitacion: 15, nieve: 0, nubosidad: 100, viento: 30, rafagas: 75, cape: 3000, codigo: 95, spc: 3, avisos: { tormenta: 1 } },
  nieve: { temperatura: -4, precipitacion: 2, nieve: 3, nubosidad: 100, viento: 25, rafagas: 50, cape: 0, codigo: 73, spc: 0, avisos: { invierno: 1 } },
  niebla: { temperatura: 10, precipitacion: 0, nieve: 0, nubosidad: 100, viento: 5, rafagas: 10, cape: 0, codigo: 45, spc: 0, avisos: {} },
  viento: { temperatura: 20, precipitacion: 0, nieve: 0, nubosidad: 60, viento: 50, rafagas: 90, cape: 200, codigo: 3, spc: 0, avisos: { otro: 1 } },
}
const NOMBRES_PRESET = { despejado: '☀ Despejado', lluvia: '🌧 Lluvia', tormenta: '⛈ Tormenta', nieve: '❄ Nieve', niebla: '🌫 Niebla', viento: '💨 Viento' }
const WMO = [[0, 'Despejado'], [1, 'Mayormente despejado'], [2, 'Parcialmente nublado'], [3, 'Nublado'], [45, 'Niebla'],
  [51, 'Llovizna'], [61, 'Lluvia ligera'], [63, 'Lluvia moderada'], [65, 'Lluvia fuerte'], [71, 'Nieve ligera'],
  [73, 'Nieve'], [75, 'Nieve fuerte'], [80, 'Chubascos'], [95, 'Tormenta eléctrica'], [96, 'Tormenta con granizo']]
const SPC = ['Sin riesgo', 'General', 'Marginal', 'Ligero', 'Aumentado', 'Moderado', 'Alto']
const GRUPOS = { tropical: 'Tropical', invierno: 'Invierno', inundacion: 'Inundación', tormenta: 'Tormenta severa', otro: 'Otro' }
const NUMEROS = [['temperatura', 'Temperatura (°C)', -50, 55, 1], ['precipitacion', 'Lluvia (mm/h)', 0, 200, 0.1],
  ['nieve', 'Nieve (cm/h)', 0, 100, 0.1], ['nubosidad', 'Nubosidad (%)', 0, 100, 1], ['viento', 'Viento (km/h)', 0, 250, 1],
  ['rafagas', 'Ráfagas (km/h)', 0, 300, 1], ['cape', 'CAPE (J/kg)', 0, 10000, 50]]

const copia = (o) => JSON.parse(JSON.stringify(o))
// Mañana a las 17:00 (hora local del navegador; se interpreta como hora local del origen).
const manana = new Date(Date.now() + 864e5)
const fechaInicial = `${manana.getFullYear()}-${String(manana.getMonth() + 1).padStart(2, '0')}-${String(manana.getDate()).padStart(2, '0')}T17:00`

const opciones = ref(null)
const errorOpciones = ref(null)
const f = reactive({ destino: 'ORD', aerolinea: 'UA', salida: fechaInicial })
const clima = reactive({ origen: copia(PRESETS.despejado), destino: copia(PRESETS.despejado) })
const preset = reactive({ origen: 'despejado', destino: 'despejado' })
const enviando = ref(false)
const error = ref(null)

onMounted(async () => {
  try {
    opciones.value = await obtenerOpciones()
    if (!opciones.value.destinos.some((d) => d.iata === f.destino)) f.destino = opciones.value.destinos[0]?.iata
  } catch (e) {
    errorOpciones.value = e.message
  }
})

function aplicar(lugar, nombre) {
  clima[lugar] = copia(PRESETS[nombre])
  preset[lugar] = nombre
}

// Un campo de avisos vacío llega como "": solo se mandan los conteos > 0.
const limpio = (c) => ({ ...c, avisos: Object.fromEntries(Object.entries(c.avisos).filter(([, n]) => Number.isInteger(n) && n > 0)) })

async function simular() {
  enviando.value = true
  error.value = null
  try {
    const v = await simularVuelo({
      origen: props.origen, destino: f.destino, aerolinea: f.aerolinea, salida_local: f.salida,
      clima_origen: limpio(clima.origen), clima_destino: limpio(clima.destino),
    })
    emit('resultado', v)
  } catch (e) {
    error.value = e.message
  } finally {
    enviando.value = false
  }
}
</script>

<template>
  <form class="simulador" @submit.prevent="simular">
    <p v-if="errorOpciones" class="aviso-error" role="alert"><strong>Error:</strong> {{ errorOpciones }}</p>
    <template v-else-if="opciones">
      <p class="sub">El modelo evalúa un vuelo hipotético desde {{ origen }} con el clima que indiques.</p>

      <div class="campo">
        <label for="sim-destino">Destino</label>
        <select id="sim-destino" v-model="f.destino" required>
          <option v-for="d in opciones.destinos" :key="d.iata" :value="d.iata">{{ d.ciudad || d.nombre }} ({{ d.iata }})</option>
        </select>
      </div>
      <div class="fila">
        <div class="campo">
          <label for="sim-aerolinea">Aerolínea</label>
          <select id="sim-aerolinea" v-model="f.aerolinea" required>
            <option v-for="a in opciones.aerolineas" :key="a.codigo" :value="a.codigo">{{ a.nombre }}</option>
          </select>
        </div>
        <div class="campo">
          <label for="sim-salida">Salida (hora local)</label>
          <input id="sim-salida" v-model="f.salida" type="datetime-local" required />
        </div>
      </div>

      <fieldset v-for="lugar in ['origen', 'destino']" :key="lugar" class="clima">
        <legend>Clima en {{ lugar === 'origen' ? origen : f.destino }} ({{ lugar }})</legend>
        <div class="presets" role="group" :aria-label="`Condiciones rápidas para el ${lugar}`">
          <button
            v-for="(texto, nombre) in NOMBRES_PRESET"
            :key="nombre"
            type="button"
            :aria-pressed="preset[lugar] === nombre"
            @click="aplicar(lugar, nombre)"
          >
            {{ texto }}
          </button>
        </div>
        <details>
          <summary>Ajustar valores</summary>
          <div class="rejilla">
            <div v-for="[clave, etiqueta, min, max, paso] in NUMEROS" :key="clave" class="campo">
              <label :for="`sim-${lugar}-${clave}`">{{ etiqueta }}</label>
              <input :id="`sim-${lugar}-${clave}`" v-model.number="clima[lugar][clave]" type="number" :min="min" :max="max" :step="paso" required @input="preset[lugar] = null" />
            </div>
            <div class="campo">
              <label :for="`sim-${lugar}-codigo`">Tipo de tiempo</label>
              <select :id="`sim-${lugar}-codigo`" v-model.number="clima[lugar].codigo" @change="preset[lugar] = null">
                <option v-for="[c, t] in WMO" :key="c" :value="c">{{ t }}</option>
              </select>
            </div>
            <div class="campo">
              <label :for="`sim-${lugar}-spc`">Riesgo de tormentas (SPC)</label>
              <select :id="`sim-${lugar}-spc`" v-model.number="clima[lugar].spc" @change="preset[lugar] = null">
                <option v-for="(t, i) in SPC" :key="i" :value="i">{{ t }}</option>
              </select>
            </div>
          </div>
          <p class="sub">Avisos del NWS vigentes (emitidos ≥ 24 h antes)</p>
          <div class="rejilla">
            <div v-for="(t, g) in GRUPOS" :key="g" class="campo">
              <label :for="`sim-${lugar}-nws-${g}`">{{ t }}</label>
              <input :id="`sim-${lugar}-nws-${g}`" v-model.number="clima[lugar].avisos[g]" type="number" min="0" max="10" step="1" placeholder="0" @input="preset[lugar] = null" />
            </div>
          </div>
        </details>
      </fieldset>

      <p v-if="error" class="aviso-error" role="alert"><strong>Error:</strong> {{ error }}</p>
      <button type="submit" class="boton-principal" :disabled="enviando">
        {{ enviando ? 'Evaluando…' : 'Simular vuelo' }}
      </button>
    </template>
    <p v-else class="cargando" role="status">Cargando opciones…</p>
  </form>
</template>
