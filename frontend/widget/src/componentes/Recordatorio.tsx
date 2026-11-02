import estilos from './Recordatorio.module.css';

// PARA REVISIÓN: pantalla sin diseño (RF07). El texto lo envía el backend en
// `advertencia_temporal` para consultas sobre fechas, inscripciones o exámenes.
export function Recordatorio({ texto }: { texto: string }) {
  return (
    <p className={estilos.recordatorio} data-para-revision>
      <strong>Importante:</strong> {texto}
    </p>
  );
}
