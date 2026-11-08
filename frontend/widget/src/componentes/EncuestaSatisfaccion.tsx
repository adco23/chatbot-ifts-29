import { useId, useState, type FormEvent } from 'react';
import type { EstadoEncuesta } from '../hooks/useConversacion';
import estilos from './EncuestaSatisfaccion.module.css';

const PUNTAJES = [
  { valor: 1, descripcion: 'nada útil' },
  { valor: 2, descripcion: 'poco útil' },
  { valor: 3, descripcion: 'más o menos útil' },
  { valor: 4, descripcion: 'útil' },
  { valor: 5, descripcion: 'muy útil' },
];
const LARGO_MAXIMO_COMENTARIO = 500;

interface Props {
  estado: EstadoEncuesta;
  alTerminar: (estado: Exclude<EstadoEncuesta, 'pendiente'>) => void;
}

// PARA REVISIÓN: pantalla sin diseño (RF11). El puntaje y el comentario no se envían a
// ningún lado: falta el endpoint (ver docs/api-contract.md).
export function EncuestaSatisfaccion({ estado, alTerminar }: Props) {
  const [puntaje, setPuntaje] = useState<number | null>(null);
  const [comentario, setComentario] = useState('');
  const id = useId();

  if (estado === 'enviada') return <p className={estilos.texto}>¡Gracias por tu opinión!</p>;
  if (estado === 'omitida') {
    return (
      <p className={estilos.texto}>Listo, omitiste la encuesta. ¡Gracias por usar el asistente!</p>
    );
  }

  const alEnviar = (evento: FormEvent) => {
    evento.preventDefault();
    if (puntaje !== null) alTerminar('enviada');
  };

  return (
    <form className={estilos.encuesta} onSubmit={alEnviar} data-para-revision>
      <fieldset className={estilos.grupo}>
        <legend className={estilos.pregunta}>¿Qué tan útil te resultó el asistente?</legend>
        <div className={estilos.puntajes}>
          {PUNTAJES.map(({ valor, descripcion }) => (
            <label key={valor} className={estilos.puntaje}>
              <input
                type="radio"
                name={`${id}-puntaje`}
                value={valor}
                checked={puntaje === valor}
                onChange={() => setPuntaje(valor)}
              />
              <span aria-hidden="true">{valor}</span>
              <span className="solo-lectores">
                {valor} de 5, {descripcion}
              </span>
            </label>
          ))}
        </div>
        <div className={estilos.extremos} aria-hidden="true">
          <span>Nada útil</span>
          <span>Muy útil</span>
        </div>
      </fieldset>

      <label htmlFor={`${id}-comentario`} className={estilos.etiqueta}>
        Comentario (opcional, sin datos personales)
      </label>
      <textarea
        id={`${id}-comentario`}
        className={estilos.comentario}
        rows={2}
        maxLength={LARGO_MAXIMO_COMENTARIO}
        value={comentario}
        onChange={(evento) => setComentario(evento.target.value)}
      />

      <div className={estilos.acciones}>
        <button type="submit" className={estilos.enviar} disabled={puntaje === null}>
          Enviar calificación
        </button>
        <button type="button" className={estilos.omitir} onClick={() => alTerminar('omitida')}>
          Omitir
        </button>
      </div>
    </form>
  );
}
