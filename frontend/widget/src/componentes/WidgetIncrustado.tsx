import { clienteChat } from '../api';
import { TEMAS } from '../config/temas';
import { useConversacion } from '../hooks/useConversacion';
import { PanelChat } from './PanelChat';
import estilos from './WidgetIncrustado.module.css';

/** Modo `incrustado`: sin lanzador, el panel siempre abierto y del tamaño del iframe. */
export function WidgetIncrustado() {
  const conversacion = useConversacion(clienteChat);
  return (
    <PanelChat
      id="asistente-panel"
      abierto
      conversacion={conversacion}
      temas={TEMAS}
      className={estilos.panel}
    />
  );
}
