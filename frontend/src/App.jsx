import { useState } from "react";
import "./App.css";

function App() {
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const analyzeMeeting = async () => {
    setLoading(true);
    setError("");

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/analyze",
        {
          method: "POST",
        }
      );

      if (!response.ok) {
        throw new Error("Failed to analyze meeting.");
      }

      const data = await response.json();

      setResults(data.notes);
    } catch (error) {
      setError(error.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Meeting Proxy</h1>
        <p>
          Analyze meeting notes against transcript evidence.
        </p>
      </header>

      <main className="container">
        <button
          className="analyze-button"
          onClick={analyzeMeeting}
          disabled={loading}
        >
          {loading ? "Analyzing..." : "Analyze Meeting"}
        </button>

        {error && (
          <div className="error">
            {error}
          </div>
        )}

        {results.length > 0 && (
          <section className="results-section">
            <h2>Meeting Results</h2>

            {results.map((note) => (
              <div
                className="note-card"
                key={note.id}
              >
                <div className="note-header">
                  <h3>{note.text}</h3>

                  <span
                    className={`status ${note.status}`}
                  >
                    {note.status}
                  </span>
                </div>

                <p>
                  <strong>Evidence:</strong>{" "}
                  {note.evidence}
                </p>

                <p>
                  <strong>Confidence:</strong>{" "}
                  {(note.confidence * 100).toFixed(0)}%
                </p>
              </div>
            ))}
          </section>
        )}
      </main>
    </div>
  );
}

export default App;
