import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import QuickEntry from './QuickEntry';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {window.location.search === '?launcher' ? <QuickEntry /> : <App />}
  </React.StrictMode>,
);
