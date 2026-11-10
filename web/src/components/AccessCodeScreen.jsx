import { useState } from "react";

const PROBLEMS = {
  wrong: "That code was not accepted.",
  "too-many": "Too many wrong codes from this network. Wait a minute, then try again.",
};

function Logo() {
  return (
    <div className="logo" aria-hidden="true">
      <span /><span /><span />
    </div>
  );
}

export default function AccessCodeScreen({ problem, onSubmit }) {
  const [code, setCode] = useState("");

  function submit(event) {
    event.preventDefault();
    if (code.trim()) onSubmit(code);
  }

  return (
    <div className="auth">
      <div className="blob b1" /><div className="blob b2" /><div className="blob b3" />
      <form className={problem ? "auth-card shake" : "auth-card"} onSubmit={submit}>
        <Logo />
        <h1>Due-Diligence Room</h1>
        <p className="auth-sub">Enter the access code to continue</p>
        {PROBLEMS[problem] && <p className="auth-error" role="alert">{PROBLEMS[problem]}</p>}
        <input
          autoFocus
          type="password"
          autoComplete="off"
          placeholder="Access code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="submit" className="auth-button" disabled={!code.trim()}>
          Enter <span className="arrow-slide">&rarr;</span>
        </button>
      </form>
    </div>
  );
}