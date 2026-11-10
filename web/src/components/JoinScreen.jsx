import { useState } from "react";

function Logo() {
  return (
    <div className="logo" aria-hidden="true">
      <span /><span /><span />
    </div>
  );
}

export default function JoinScreen({ connected, onJoin, initialName }) {
  const [name, setName] = useState(initialName || "");

  function submit(event) {
    event.preventDefault();
    if (name.trim()) onJoin(name);
  }

  return (
    <div className="auth">
      <div className="blob b1" /><div className="blob b2" /><div className="blob b3" />
      <form className="auth-card" onSubmit={submit}>
        <Logo />
        <h1>Due-Diligence Room</h1>
        <p className="auth-sub">
          Ask questions about Apple's FY2025 10-K together. Everyone here sees each
          question, how it's being answered, and votes on any action before it runs.
        </p>
        <div className="auth-chips">
          <span>Cited answers</span>
          <span>Confidence scores</span>
          <span>Team approval</span>
        </div>
        <input
          autoFocus
          maxLength={40}
          placeholder="Your name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button type="submit" className="auth-button" disabled={!connected || !name.trim()}>
          {connected ? (
            <>Join the room <span className="arrow-slide">&rarr;</span></>
          ) : (
            <><span className="dot-pulse" /> Connecting...</>
          )}
        </button>
      </form>
    </div>
  );
}