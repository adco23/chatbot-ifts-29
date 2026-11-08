import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './estilos/tokens.css';
import './estilos/base.css';
import { App } from './App';

// Solo en `npm run dev`: marca con contorno punteado las pantallas sin diseño (para revisión).
if (import.meta.env.DEV) document.documentElement.dataset.revision = 'visible';

const raiz = document.getElementById('raiz');
if (!raiz) throw new Error('Falta el elemento #raiz en index.html.');

createRoot(raiz).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
