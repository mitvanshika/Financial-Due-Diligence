import { useCallback, useEffect, useRef, useState } from "react";

// One WebSocket to the room (/ws on the same host that served this page).
// The server sends the WHOLE room state after every change, so this hook only
// keeps the latest copy - there is nothing to merge and nothing to drift.
//
// If the connection drops (server restart, Wi-Fi blip) it reconnects every
// two seconds and re-joins under the same name automatically.
//
// Access code: every connection first sends {"type":"auth","code":...} (an
// empty code if none is saved yet; a room without a code ignores it). The
// server closes with 4401 for a wrong or missing code and 4429 after too many
// wrong ones - then this stops reconnecting and asks for the code. The code
// lives in sessionStorage: kept across refreshes, gone when the tab closes.

const NAME_KEY = "dd-room-name";
const CODE_KEY = "dd-room-code";
const RECONNECT_MS = 2000;
const CLOSE_BAD_CODE = 4401;
const CLOSE_TOO_MANY = 4429;

function socketUrl() {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

function savedName() {
  try {
    return window.localStorage.getItem(NAME_KEY) || "";
  } catch {
    return "";
  }
}

function saveName(name) {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    // Private mode etc. - you'll just be asked for your name again.
  }
}

function savedCode() {
  try {
    return window.sessionStorage.getItem(CODE_KEY) || "";
  } catch {
    return "";
  }
}

function saveCode(code) {
  try {
    if (code) window.sessionStorage.setItem(CODE_KEY, code);
    else window.sessionStorage.removeItem(CODE_KEY);
  } catch {
    // Private mode etc. - you'll just be asked for the code again.
  }
}

export function useRoom() {
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState(null);
  const [you, setYou] = useState(null);
  const [error, setError] = useState(null);
  const [name, setName] = useState(savedName);
  // null = no code needed right now; "needed" | "wrong" | "too-many" = ask for it.
  const [access, setAccess] = useState(null);

  const socketRef = useRef(null);
  const connectRef = useRef(null);
  const codeRef = useRef(savedCode());
  const nameRef = useRef(name);
  nameRef.current = name;

  useEffect(() => {
    let closedOnPurpose = false;
    let retryTimer = null;

    function connect() {
      const ws = new WebSocket(socketUrl());
      socketRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        ws.send(JSON.stringify({ type: "auth", code: codeRef.current }));
        if (nameRef.current) {
          ws.send(JSON.stringify({ type: "join", name: nameRef.current }));
        }
      };
      ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "state") {
          setState(msg.state);
          setYou(msg.you);
        } else if (msg.type === "error") {
          setError({ message: msg.message, at: Date.now() });
        }
      };
      ws.onclose = (event) => {
        setConnected(false);
        setYou(null);
        if (closedOnPurpose) return;
        // Retrying by itself would only use up the allowed wrong codes.
        if (event.code === CLOSE_BAD_CODE) {
          setAccess(codeRef.current ? "wrong" : "needed");
          codeRef.current = "";
          saveCode("");
        } else if (event.code === CLOSE_TOO_MANY) {
          setAccess("too-many");
        } else {
          retryTimer = setTimeout(connect, RECONNECT_MS);
        }
      };
    }

    connectRef.current = connect;
    connect();
    return () => {
      closedOnPurpose = true;
      clearTimeout(retryTimer);
      socketRef.current?.close();
    };
  }, []);

  const send = useCallback((message) => {
    const ws = socketRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
    } else {
      setError({ message: "not connected to the room - reconnecting...", at: Date.now() });
    }
  }, []);

  const join = useCallback(
    (newName) => {
      const trimmed = newName.trim();
      if (!trimmed) return;
      saveName(trimmed);
      setName(trimmed);
      send({ type: "join", name: trimmed });
    },
    [send],
  );

  const submitCode = useCallback((code) => {
    const trimmed = code.trim();
    if (!trimmed) return;
    codeRef.current = trimmed;
    saveCode(trimmed);
    setAccess(null);
    connectRef.current?.();
  }, []);

  return {
    connected,
    state,
    you,
    name,
    error,
    access,
    clearError: () => setError(null),
    submitCode,
    join,
    ask: (text) => send({ type: "ask", text }),
    remove: (questionId) => send({ type: "remove", question_id: questionId }),
    propose: (kind, questionId) => send({ type: "propose", kind, question_id: questionId }),
    vote: (actionId, approve) => send({ type: "vote", action_id: actionId, approve }),
  };
}
