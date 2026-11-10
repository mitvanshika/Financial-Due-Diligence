import { useState } from "react";
import { ACTION_LABELS, WithMarkers, stepLabel, timeOf } from "../format.jsx";

function Arrow() {
  return (
    <svg className="arrow" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 12h15M13 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="1.8"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function scoreText(score) {
  return typeof score === "number" ? score.toFixed(2) : String(score);
}

// The score is 0-1; the bar under it is just that number as a percentage.
function scorePercent(score) {
  const n = Number(score);
  if (Number.isNaN(n)) return 0;
  return Math.max(0, Math.min(1, n)) * 100;
}

// The big tile on the left: who asked, the confidence score, the question.
function QuestionTile({ item }) {
  const a = item.answer;
  const c = a?.confidence;
  return (
    <div className={`tile tile-${c ? c.band : "none"}`}>
      <div className="tile-label">Asked by {item.asked_by}</div>
      {c ? (
        <>
          <div className="tile-score">{scoreText(c.score)}</div>
          <div className="tile-band">{c.band} confidence</div>
        </>
      ) : (
        <div className="tile-score">&mdash;</div>
      )}
      <h3 className="tile-title">{item.text}</h3>
      {c && <div className="meter"><span style={{ width: `${scorePercent(c.score)}%` }} /></div>}
      <div className="tile-foot">
        <span>{timeOf(item.asked_at)}</span>
        {a && <span>{a.elapsed_s}s &middot; {a.attempts.length} search attempt(s)</span>}
      </div>
    </div>
  );
}

// The three search signals behind the score. An answer citing a database
// figure ([S1]) gets its score from that figure, not from these, so they are
// hidden then - showing them would only confuse.
function Signals({ confidence, citations }) {
  const fromFigures = citations.some((cit) => cit.marker.startsWith("[S"));
  if (!confidence || fromFigures) return null;
  const c = confidence;
  return (
    <div className="signals">
      <div className="signal"><b>{String(c.hybrid_agreement)}</b><span>Both searches agreed</span></div>
      <div className="signal"><b>{String(c.rerank_strength)}</b><span>Reranker strength</span></div>
      <div className="signal"><b>{String(c.loop_cleanliness)}</b><span>First-try result</span></div>
    </div>
  );
}

// The start of a passage, with "more" to read all of it in place. The full
// text keeps one table row per line; the short version is one running line,
// minus the long "─" rules tables are drawn with.
function PassageText({ passage }) {
  const [open, setOpen] = useState(false);
  const full = passage.text;
  const short = passage.snippet.replace(/─{3,}/g, " ");
  // Old answers (asked before the full text was sent) have no `text`.
  if (!full) return <div className="small">{short}...</div>;
  if (short.length >= full.replace(/\s+/g, " ").length) {
    return <div className="small passage-text">{full}</div>;
  }
  return (
    <div className="small passage-text">
      {open ? full : `${short}...`}
      <button type="button" className="link" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "less" : "more"}
      </button>
    </div>
  );
}

function ActionButtons({ item, actions, actionPending, onPropose }) {
  const a = item.answer;
  if (!a) return null;
  return (
    <div className="action-buttons">
      {actions.map((kind) => {
        const notAllowed = !a.answered && kind !== "flag";
        return (
          <button
            key={kind}
            className="secondary action-btn"
            disabled={actionPending || notAllowed}
            title={
              actionPending
                ? "The room is already deciding on an action"
                : notAllowed
                  ? "Only a flag can act on an unanswered question"
                  : "Propose this - everyone in the room must approve it"
            }
            onClick={() => onPropose(kind, item.id)}
          >
            {ACTION_LABELS[kind] || kind} <Arrow />
          </button>
        );
      })}
    </div>
  );
}

// One finished question: a tile with the score, then the answer, its
// references, the signals behind the score, and the passages and steps.
export default function QuestionCard({ item, actions, actionPending, onPropose }) {
  const a = item.answer;
  return (
    <article className="qcard">
      <QuestionTile item={item} />

      <div className="qbody">
        {item.error && <p className="error-text">The pipeline failed on this question: {item.error}</p>}

        {a && (
          <>
            {a.answered ? (
              <p className="answer"><WithMarkers text={a.text} /></p>
            ) : (
              <p className="refused">Not answered: {a.text}</p>
            )}

            {a.citations.length > 0 && (
              <section>
                <h4 className="section-label">References</h4>
                <ul className="refs">
                  {a.citations.map((c) => (
                    <li key={c.marker} className="ref">
                      <span className="ref-marker">{c.marker}</span>
                      <span className="ref-source">{c.source}</span>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <Signals confidence={a.confidence} citations={a.citations} />
            {a.confidence?.notes?.length > 0 && (
              <ul className="notes">
                {a.confidence.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            )}

            {a.passages.length > 0 && (
              <details>
                <summary>Search results ({a.passages.length} passages graded)</summary>
                <ul className="passages">
                  {a.passages.map((p, i) => (
                    <li key={i} className="passage">
                      <div
                        className={p.relevance >= 0.5 ? "rel rel-yes" : "rel"}
                        title="How well the grader judged this passage answers the question (0-1). Not the answer's confidence."
                      >
                        <b>{p.relevance.toFixed(2)}</b>
                        <span>relevance</span>
                      </div>
                      <div className="passage-main">
                        <strong>{p.where}</strong>
                        <PassageText passage={p} />
                        <div className="muted small">grader: {p.reason}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}

        {item.steps?.length > 0 && (
          <details>
            <summary>How it was answered ({item.steps.length} steps)</summary>
            <ol className="steps">
              {item.steps.map((s, i) => <li key={i}>{stepLabel(s)}</li>)}
            </ol>
          </details>
        )}

        <ActionButtons item={item} actions={actions} actionPending={actionPending} onPropose={onPropose} />
      </div>
    </article>
  );
}