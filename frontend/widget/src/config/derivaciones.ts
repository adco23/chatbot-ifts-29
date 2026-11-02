export interface Derivacion {
  sector: string;
  correo: string;
  horario: string;
}

/**
 * RF05. La respuesta `sin_evidencia` del backend no trae datos de derivación, así que el
 * widget muestra este sector. Copiado de backend/app/config/derivaciones.json.
 * PENDIENTE: que el backend devuelva el sector según la consulta.
 */
export const DERIVACION_POR_DEFECTO: Derivacion = {
  sector: 'Bedelía',
  correo: 'bedelia@ifts29.edu.ar',
  horario: 'Lunes a viernes de 18:30 a 21:30',
};
