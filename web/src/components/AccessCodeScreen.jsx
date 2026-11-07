import { useState } from "react";

const PROBLEMS = {
  wrong: "That code was not accepted.",
  "too-many": "Too many wrong codes from this network. Wait a minute, then try again.",
};

export default function AccessCodeScreen({ problem, onSubmit }) {
  const [code, setCode] = useState("");

  function submit(event) {
    event.preventDefault();
    if (code.trim()) onSubmit(code);
  }

  return (
    <div className="join">
      <h1>Due-Diligence Room</h1>
      <p className="muted">Enter access code</p>
      {PROBLEMS[problem] && <p className="error-text">{PROBLEMS[problem]}</p>}
      <form onSubmit={submit}>
        <input
          autoFocus
          type="password"
          autoComplete="off"
          placeholder="Access code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="submit" disabled={!code.trim()}>
          Enter
        </button>
      </form>
    </div>
  );
}
