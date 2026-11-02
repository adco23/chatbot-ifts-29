export interface Tema {
  /** Texto del chip; se envía como mensaje del estudiante. */
  texto: string;
  /** Slug alineado con el campo `categoria` del modelo de datos. El contrato actual no lo recibe. */
  slug: string;
}

/** Menú completo. La tercera captura muestra un solo chip: intención a confirmar. */
export const TEMAS: readonly Tema[] = [
  { texto: '1. Primeros pasos y Moodle', slug: 'primeros-pasos-moodle' },
  { texto: '2. Mi cursada', slug: 'mi-cursada' },
  { texto: '3. Asistencia y entregas', slug: 'asistencia-entregas' },
  { texto: '4. Plan de estudios', slug: 'plan-de-estudios' },
  { texto: '5. Trámites y SIU', slug: 'tramites-siu' },
  { texto: '6. Ayuda técnica', slug: 'ayuda-tecnica' },
];
