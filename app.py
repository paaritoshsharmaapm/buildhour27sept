"""Mutual Fund FAQ Assistant — the demo surface (P15, PRD FR-6).

Presentation only (I8). This file imports from `src.pipeline`, `src.config` and
`src.store` and nothing else. In particular it never imports the retriever, the
generator or the formatter, because the eval and the demo must exercise the same
code path: business logic re-implemented here would be code that is graded in
one place and demoed in another.

All prompt-injection defences and numeric checks live upstream of this file. The
UI's job is to show the status the pipeline returned and never to soften it.
"""

from __future__ import annotations

import streamlit as st

from src.config import CONFIG
from src.pipeline import answer

st.set_page_config(page_title="Mutual Fund FAQ Assistant", layout="centered")

# Absorb the MiniLM and Chroma warm-up into the startup screen rather than the
# first question, so the first turn is not noticeably slower than the second.
try:
    from src.store import StoreMissing, get_collection

    get_collection()
except StoreMissing:
    st.error("The chunk store is missing or empty. Build it first:")
    st.code("python -m src.ingest", language="bash")
    st.stop()
except Exception as error:
    st.error(f"Could not open the chunk store: {type(error).__name__}")
    st.code("python -m src.ingest --force", language="bash")
    st.stop()

# Deliberately fixed string, graded as deliverable D6. Rendered directly under
# the input so it is visible without scrolling.
DISCLAIMER = "Facts-only. No investment advice."

EXAMPLE_QUESTIONS = [
    "What is the expense ratio of HDFC Large Cap Fund?",
    "What is the lock-in period of HDFC ELSS Tax Saver Fund?",
    "How do I download the statement for HDFC Flexi Cap Fund?",
]

SCHEME_LINE = (
    "Large Cap, Flexi Cap, ELSS Tax Saver, Small Cap and Balanced Advantage "
    "(equity, tax-saving, and hybrid)."
)


def _render_turn(result) -> None:
    """Render one exchange. Status drives the styling, never the reverse."""
    status = result.status

    if status == "REFUSED_PII":
        # No citation and no echo of what was typed (I3). The user's own message
        # is already in the transcript above; repeating it here would put the
        # PAN back on screen.
        st.info(result.text, icon="🔒")
        return

    if status in {"REFUSED_ADVICE", "REFUSED_OUT_OF_SCOPE", "REFUSED_PERFORMANCE"}:
        st.info(result.text, icon="ℹ️")
        return

    if status == "NO_GROUNDING":
        st.warning(result.text, icon="⚠️")
        return

    if status == "ERROR":
        st.error(result.text, icon="⚠️")
        return

    # ANSWERED. markdown, not write, so the citation renders as a real link.
    st.markdown(result.text)


def _render_sidebar(result) -> None:
    with st.sidebar:
        st.header("How this answer was produced")
        st.caption(
            f"Model: `{CONFIG.GROQ_MODEL}` · grounding threshold "
            f"{CONFIG.GROUNDING_THRESHOLD:.2f} · top {CONFIG.TOP_K} of "
            f"{CONFIG.RETRIEVE_POOL} candidates"
        )
        latency = result.latency_ms or {}
        if latency:
            st.caption(
                "  ·  ".join(f"{stage.replace('_', ' ')}: {ms} ms"
                             for stage, ms in latency.items())
            )

        with st.expander("Retrieval trace"):
            trace = result.trace or {}
            hits = (trace.get("retrieval") or {}).get("hits") or []
            if not hits:
                st.caption("No retrieval evidence — this question was answered "
                           "before retrieval ran.")
            else:
                st.dataframe(
                    [
                        {
                            "rank": hit["rank"],
                            "score": hit["score"],
                            "section": (hit.get("section") or "")[:28],
                            "source_id": hit.get("source_id"),
                        }
                        for hit in hits
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
                with st.expander("Chunk previews"):
                    for hit in hits:
                        st.markdown(f"**{hit['rank']}. {hit.get('source_id')}** "
                                    f"— {hit['chars']} chars")
                        st.caption(hit.get("preview", ""))
                scheme = trace.get("scheme")
                if scheme and scheme != "unresolved":
                    st.caption(f"Scheme filter: `{scheme}`")

        with st.expander("Sources consulted"):
            hits = (result.trace or {}).get("retrieval", {}).get("hits") or []
            seen: set = set()
            rows = []
            for hit in hits:
                source_id = hit.get("source_id")
                if source_id in seen:
                    continue
                seen.add(source_id)
                rows.append((source_id, hit.get("title") or source_id, hit.get("url")))
            if result.citation is not None:
                cited_url = result.citation.url
                if cited_url not in [url for _, _, url in rows]:
                    rows.insert(0, ("cited", result.citation.title, cited_url))
            if not rows:
                st.caption("No sources were consulted for this question.")
            for source_id, title, url in rows:
                if url:
                    st.markdown(f"- [{title}]({url}) · `{source_id}`")
                else:
                    st.markdown(f"- {title} · `{source_id}`")


def main() -> None:
    st.title("Mutual Fund FAQ Assistant")
    st.markdown(
        "Hi! I answer facts about 5 HDFC Mutual Fund schemes using official "
        "sources only."
    )
    st.caption(SCHEME_LINE)

    if "turns" not in st.session_state:
        st.session_state.turns = []
        st.session_state.pending = ""

    # Exactly three chips: fees, ELSS lock-in, statements (PRD §6).
    columns = st.columns(len(EXAMPLE_QUESTIONS))
    for column, question in zip(columns, EXAMPLE_QUESTIONS):
        if column.button(question, key=f"chip-{question[:16]}", use_container_width=True):
            st.session_state.pending = question
            st.rerun()

    for question, result in st.session_state.turns:
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            _render_turn(result)
        _render_sidebar(result)

    typed = st.chat_input("Ask a scheme fact, e.g. expense ratio or exit load")
    question = typed or st.session_state.pending
    if question:
        st.session_state.pending = ""
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                result = answer(question, debug=True)
            except Exception as error:
                # I9: a traceback is a failure the user can see. Say what broke
                # in plain terms instead.
                st.error(
                    "Something went wrong handling that question "
                    f"({type(error).__name__}). Please try rephrasing it."
                )
                st.stop()
            _render_turn(result)
        st.session_state.turns.append((question, result))
        _render_sidebar(result)

    # D6, rendered directly under the input and above the fold.
    st.markdown(f"*{DISCLAIMER}*")


if __name__ == "__main__":
    main()
