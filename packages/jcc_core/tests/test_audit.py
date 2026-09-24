import os
import sqlite3
import tempfile

from jcc_core import audit


def _tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.remove(path)
    return path


def test_append_links_to_genesis_and_prev():
    path = _tmp_db()
    conn = sqlite3.connect(path)
    conn.executescript(audit.SCHEMA)
    conn.commit()
    e1 = audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s",
                            agent="a", project="p", assessment="x", directive="ESCALATE:d1",
                            evidence_hash="h1")
    e2 = audit.append_audit(conn, event_id="e2", timestamp=2.0, session_id="s",
                            agent="a", project="p")
    assert e1["prev_hash"] == audit.GENESIS
    assert e2["prev_hash"] == e1["entry_hash"]
    ok, count = audit.verify_chain(conn)
    assert ok and count == 2
    conn.close()
    os.remove(path)


def test_tamper_detected():
    path = _tmp_db()
    conn = sqlite3.connect(path)
    conn.executescript(audit.SCHEMA)
    conn.commit()
    audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s", agent="a", project="p")
    audit.append_audit(conn, event_id="e2", timestamp=2.0, session_id="s", agent="a", project="p")
    conn.execute("UPDATE audit_tape SET assessment='TAMPERED' WHERE seq=1")
    conn.commit()
    ok, bad = audit.verify_chain(conn)
    assert ok is False
    assert bad == 1
    conn.close()
    os.remove(path)


def test_append_only_semantics():
    # No delete/update API is exposed; immutability is structural (PK + hash chain).
    path = _tmp_db()
    conn = sqlite3.connect(path)
    conn.executescript(audit.SCHEMA)
    conn.commit()
    audit.append_audit(conn, event_id="e1", timestamp=1.0, session_id="s", agent="a", project="p")
    audit.append_audit(conn, event_id="e2", timestamp=2.0, session_id="s", agent="a", project="p")
    audit.append_audit(conn, event_id="e3", timestamp=3.0, session_id="s", agent="a", project="p")
    ok, count = audit.verify_chain(conn)
    assert ok and count == 3
    conn.close()
    os.remove(path)
