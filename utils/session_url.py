"""Chat-session id in the URL (`?s=<id>`) for sidebar navigation.

Kept separate from file links: sidebar buttons sync it, the app adopts it
on load (refresh-safe, shareable links).
"""
import streamlit as st


def sync_session_url(sid) -> None:
    """Reflect the active chat session in the URL (no-op if already set).

    NOTE: writing st.query_params only updates the URL — it does NOT
    rerun the app. The caller must call st.rerun() itself when the UI
    needs refreshing after navigation.
    """
    try:
        if not sid:
            return
        qp = st.query_params
        if qp.get("s") != sid:
            qp["s"] = sid
    except Exception:
        pass


def read_session_param():
    """Return the `s` query param, if any."""
    try:
        return st.query_params.get("s") or None
    except Exception:
        return None
