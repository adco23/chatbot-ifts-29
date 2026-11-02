/** Lectura centralizada de variables de entorno (todas públicas: no hay secretos en el front). */

export const URL_API = (import.meta.env.VITE_API_URL ?? '').trim();

/** Solo en `npm run dev` y sin variable configurada: moodle-sim local. */
const ORIGEN_PADRE_DESARROLLO = 'http://localhost:5174';

export const ORIGENES_PADRE: readonly string[] = (
  import.meta.env.VITE_ORIGENES_PADRE ?? (import.meta.env.DEV ? ORIGEN_PADRE_DESARROLLO : '')
)
  .split(',')
  .map((origen) => origen.trim().replace(/\/+$/, ''))
  .filter(Boolean);

/** RF14, complementario. */
export const PREGUNTAS_SUGERIDAS = import.meta.env.VITE_PREGUNTAS_SUGERIDAS === 'true';
