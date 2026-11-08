import type { Ref } from 'react';
import { IconoCerrar, IconoRobot } from './iconos';
import estilos from './Lanzador.module.css';

interface Props {
  abierto: boolean;
  conInsignia: boolean;
  idPanel: string;
  alPulsar: () => void;
  ref?: Ref<HTMLButtonElement>;
}

export function Lanzador({ abierto, conInsignia, idPanel, alPulsar, ref }: Props) {
  return (
    <button
      ref={ref}
      type="button"
      className={estilos.lanzador}
      aria-expanded={abierto}
      aria-controls={idPanel}
      onClick={alPulsar}
    >
      <span className="solo-lectores">
        {abierto ? 'Cerrar el asistente virtual' : 'Abrir el asistente virtual'}
      </span>
      {abierto ? <IconoCerrar tamano={18} /> : <IconoRobot tamano={22} />}
      {conInsignia && !abierto && (
        <span className={estilos.insignia}>
          <span aria-hidden="true">1</span>
          <span className="solo-lectores">, 1 mensaje nuevo</span>
        </span>
      )}
    </button>
  );
}
