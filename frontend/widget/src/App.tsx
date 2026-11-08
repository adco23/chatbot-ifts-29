import { WidgetFlotante } from './componentes/WidgetFlotante';
import { WidgetIncrustado } from './componentes/WidgetIncrustado';
import { leerModo } from './embebido/modo';

const modo = leerModo();

export function App() {
  return modo === 'incrustado' ? <WidgetIncrustado /> : <WidgetFlotante />;
}
