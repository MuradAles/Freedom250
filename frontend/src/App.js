import { useState } from 'react';
import './App.css';
import Dashboard from './components/Dashboard';
import DetailView from './components/DetailView';
import CitationDrawer from './components/CitationDrawer';

/** State-based router: dashboard <-> detail by selected borrower_id.
 * Owns citation drawer state so any component can request a citation open. */
function App() {
  const [selectedId, setSelectedId] = useState(null);
  const [citation, setCitation] = useState(null);

  return (
    <div className="App">
      <header className="app-header">
        <h1>SBA Loan Compliance Checker</h1>
      </header>
      <main className="app-main">
        {selectedId ? (
          <DetailView
            borrowerId={selectedId}
            onBack={() => setSelectedId(null)}
            onOpenCitation={setCitation}
          />
        ) : (
          <Dashboard onSelect={setSelectedId} />
        )}
      </main>
      <CitationDrawer citation={citation} onClose={() => setCitation(null)} />
    </div>
  );
}

export default App;
