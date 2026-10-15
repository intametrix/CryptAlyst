import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';

const container = document.getElementById('root');
if (container === null) {
  throw new Error('index.html is missing #root');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
