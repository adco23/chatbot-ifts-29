import type { DerivacionChat } from '../api';

/**
 * RF05. Respaldo para `sin_evidencia`: cuando la consulta no coincide con ningún sector, el
 * backend no trae datos de derivación y el widget ofrece igual este contacto. Con
 * `estado = "derivada"` se usan los datos que manda el backend.
 * Copiado de backend/app/config/derivaciones.json.
 */
export const DERIVACION_POR_DEFECTO: DerivacionChat = {
  sector: 'Bedelía',
  correo: 'bedelia@ifts29.edu.ar',
  horario: 'Lunes a viernes de 18:30 a 21:30',
  responsable: null,
};
