<script setup>
// Tarjeta flotante del vuelo elegido: probabilidad y los factores que más la mueven.
import { computed } from 'vue'
import { NIVELES, hora, porcentaje } from '../util.js'

const props = defineProps({
  vuelo: { type: Object, required: true },
  origen: { type: String, required: true },
})
defineEmits(['cerrar'])

const nivel = computed(() => NIVELES[props.vuelo.nivel])
const f = computed(() => props.vuelo.factores)
// Barras proporcionales al factor de mayor peso (en valor absoluto) de este vuelo.
const maximo = computed(() =>
  Math.max(0.01, ...[...(f.value?.suben ?? []), ...(f.value?.bajan ?? [])].map((x) => Math.abs(x.peso))),
)
const ancho = (x) => `${Math.round((Math.abs(x.peso) / maximo.value) * 100)}%`
const ICONO = { clima: '☁', vuelo: '✈' }

</script>

<template>
  <section class="tarjeta-factores vidrio" aria-labelledby="titulo-factores">
    <header>
      <div>
        <h2 id="titulo-factores">{{ vuelo.numero }} · {{ origen }} → {{ vuelo.destino?.iata }}</h2>
        <p class="sub">{{ vuelo.aerolinea_nombre ?? vuelo.aerolinea }} · sale {{ hora(vuelo.salida_local) }}</p>
      </div>
      <button type="button" class="cerrar" aria-label="Cerrar" @click="$emit('cerrar')">×</button>
    </header>

    <div class="riesgo">
      <strong :style="{ color: nivel.color }">{{ porcentaje(vuelo.probabilidad) }}</strong>
      <span>{{ nivel.icono }} {{ nivel.texto }} de llegar con 15+ min de retraso</span>
    </div>
    <p v-if="vuelo.clima_aproximado" class="nota" role="status">
      ⚠ Clima aproximado (respaldo): Open-Meteo Previous Runs no respondió y se usó el pronóstico actual.
    </p>

    <template v-if="f">
      <h3 v-if="f.suben.length">Lo que sube el riesgo</h3>
      <ul class="factores">
        <li v-for="x in f.suben" :key="x.texto">
          <span class="icono" aria-hidden="true">{{ ICONO[x.tipo] }}</span>
          <span class="texto">{{ x.texto }}</span>
          <span class="barra"><span class="relleno sube" :style="{ width: ancho(x) }"></span></span>
        </li>
      </ul>
      <h3 v-if="f.bajan.length">Lo que lo baja</h3>
      <ul class="factores">
        <li v-for="x in f.bajan" :key="x.texto">
          <span class="icono" aria-hidden="true">{{ ICONO[x.tipo] }}</span>
          <span class="texto">{{ x.texto }}</span>
          <span class="barra"><span class="relleno baja" :style="{ width: ancho(x) }"></span></span>
        </li>
      </ul>
      <p class="nota">
        Son los factores que más mueven la estimación del modelo, no la causa confirmada: esa solo se conoce
        después del vuelo.
      </p>
    </template>
    <p v-else class="nota">Actualiza los vuelos para ver los factores de riesgo.</p>
  </section>
</template>
