import streamlit as st
import json
import pandas as pd
import re
from snowflake.snowpark.context import get_active_session
from bin_request_retrieval import fetch_bin_requests, mark_request_read
from call_here_api import (
    call_routing_here_api,
    call_routing_here_api_v7,
    call_geocoding_here_api,
    decode_polyline,
    decode_shape,
    display_map,
)

session = get_active_session()

API_ENDPOINT           = "/api/v2/cortex/agent:run"
API_TIMEOUT            = 50_000    # milliseconds
CORTEX_SEARCH_SERVICES = "pnp.etremblay.sales_conversation_search"
SEMANTIC_MODELS        = "@pnp.etremblay.models/sales_metrics_model.yaml"
CORTEX_MODEL           = "claude-4-sonnet"


def process_sse_response(events):
    text, sql, citations = "", "", []
    for evt in events:
        if evt.get("event") == "message.delta":
            for c in evt["data"]["delta"].get("content", []):
                if c["type"] == "text":
                    text += c["text"]
                elif c["type"] == "tool_results":
                    for r in c["tool_results"]["content"]:
                        if r["type"] == "json":
                            j = r["json"]
                            text += j.get("text", "")
                            sql   = j.get("sql", sql)
                            for sr in j.get("searchResults", []):
                                citations.append({
                                    "source_id": sr.get("source_id",""),
                                    "doc_id":    sr.get("doc_id","")
                                })
    return text, sql, citations


def run_snowflake_query(sql):
    try:
        return session.sql(sql)
    except Exception as e:
        st.error(f"SQL error: {e}")
        return None


def extract_addresses(text):
    prompt = (
        "Extract every full street address from this text and output only "
        "a JSON array of strings (no markdown). Example:\n"
        '["123 Main St City, ST 12345", "456 Rue Example Montréal QC H2X 1Y4"]\n\n'
        f"Text:\n```{text}```"
    )
    payload = {
        "model": CORTEX_MODEL,
        "messages": [
            {"role":"user","content":[{"type":"text","text":prompt}]}
        ],
    }
    resp = _snowflake.send_snow_api_request(
        "POST", API_ENDPOINT, {}, {}, payload, None, API_TIMEOUT
    )
    if resp.get("status") != 200:
        st.error(f"Agent error: {resp.get('status')}")
        return []
    try:
        events = json.loads(resp.get("content","[]"))
    except json.JSONDecodeError:
        return []
    full_text, _, _ = process_sse_response(events)
    cleaned = re.sub(r"```(?:json)?","", full_text, flags=re.IGNORECASE).strip()
    m = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return []


def geocode_address(addr):
    try:
        geo = call_geocoding_here_api(addr)
        items = geo.get("items") or []
        if not items:
            return None, None
        pos = items[0]["position"]
        return pos["lat"], pos["lng"]
    except Exception as e:
        st.error(f"Geocoding failed: {e}")
        return None, None


def handle_address_logic(query, assistant_text):
    addresses = extract_addresses(query)
    if not addresses:
        m = re.search(r"between\s+(.*?)\s+and\s+(.*)", query, flags=re.IGNORECASE)
        if m:
            addresses = [m.group(1).strip(" ,."), m.group(2).strip(" ,.")]
    if len(addresses) == 1:
        lat, lon = geocode_address(addresses[0])
        if lat is not None:
            st.write(f"📍 Map for: **{addresses[0]}**")
            st.map(pd.DataFrame({"lat":[lat],"lon":[lon]}))
    elif len(addresses) == 2:
        lat1, lon1 = geocode_address(addresses[0])
        lat2, lon2 = geocode_address(addresses[1])
        if None in (lat1, lon1, lat2, lon2):
            st.error("Could not geocode one or both addresses.")
            return
        try:
            here_v8 = call_routing_here_api((lat1, lon1),(lat2, lon2))
            coords  = decode_polyline(here_v8)
        except Exception:
            here_v7 = call_routing_here_api_v7((lat1, lon1),(lat2, lon2))
            coords   = decode_shape(here_v7)
        display_map(coords)


def snowflake_api_call(query, limit=10):
    payload = {
        "model": "claude-4-sonnet",
        "messages": [{"role":"user","content":[{"type":"text","text":query}]}],
        "tools": [
            {"tool_spec":{"type":"cortex_analyst_text_to_sql","name":"analyst1"}},
            {"tool_spec":{"type":"cortex_search","name":"search1"}},
            {"tool_spec":{"type":"http_request","name":"here_maps"}}
        ],
        "tool_resources": {
            "analyst1":{"semantic_model_file":SEMANTIC_MODELS},
            "search1":{
                "name":CORTEX_SEARCH_SERVICES,
                "max_results":limit,
                "id_column":"conversation_id"
            }
        }
    }
    try:
        resp = _snowflake.send_snow_api_request(
            "POST", API_ENDPOINT, {}, {}, payload, None, API_TIMEOUT
        )
        if resp["status"] != 200:
            st.error(f"HTTP Error: {resp['status']}")
            return None
        return json.loads(resp["content"])
    except Exception as e:
        st.error(f"Request error: {e}")
        return None


def main():
    st.title("Webinar Intelligent Sales Assistant")

    tab1, tab2 = st.tabs(["Review Requests","Assistant"])

    # ── Tab 1: Review new bin requests ─────────────────────────────────
    with tab1:
        st.header("📥 Review New Bin Requests")
        if "req_idx" not in st.session_state:
            st.session_state.req_idx = 0

        requests = fetch_bin_requests()
        idx      = st.session_state.req_idx

        if not requests:
            st.write("✅ No new bin requests.")
        elif idx >= len(requests):
            st.write("🎉 All requests reviewed.")
        else:
            req = requests[idx]
            mid = req["message_id"]

            st.subheader(f"Request {idx+1}/{len(requests)}: {mid}")
            st.markdown(f"> {req['raw_body']}")

            # DEBUG: show entire dict so we can see what's actually returned
            st.write("🔍 Full request dict:", req)

            # DEBUG: show raw Cortex JSON
            if "json_output" in req:
                try:
                    parsed = json.loads(req["json_output"])
                except Exception:
                    parsed = req["json_output"]
                st.json(parsed)

            # prefill from parsed JSON (or blank)
            fmt   = st.text_input("Container Format", value=req.get("container_format",""), key=f"fmt_{mid}")
            qty   = st.text_input("Quantity",         value=req.get("quantity",""),         key=f"qty_{mid}")
            date  = st.text_input("Date Needed",      value=req.get("date_needed",""),      key=f"date_{mid}")
            user  = st.text_input("Requester",        value=req.get("requester",""),        key=f"req_{mid}")

            c1, c2, c3 = st.columns(3)
            if c1.button("✅ Approve", key=f"app_{mid}"):
                mark_request_read(mid)
                st.success("Approved")
            if c2.button("❌ Reject", key=f"rej_{mid}"):
                mark_request_read(mid)
                st.warning("Rejected")
            if c3.button("➡️ Next", key=f"next_{mid}"):
                st.session_state.req_idx += 1

    # ── Tab 2: Chat + address mapping ───────────────────────────────────
    with tab2:
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for msg in st.session_state.messages:
            prefix = "**You:**" if msg["role"]=="user" else "**Assistant:**"
            st.markdown(f"{prefix} {msg['content']}")

        query = st.text_input("Your question:", key="chat_input")
        if st.button("Send", key="chat_send") and query:
            st.session_state.messages.append({"role":"user","content":query})

            resp = _snowflake.send_snow_api_request(
                "POST", API_ENDPOINT, {}, {}, {
                    "model": CORTEX_MODEL,
                    "messages":[{"role":"user","content":[{"type":"text","text":query}]}]
                }, None, API_TIMEOUT
            )
            try:
                events = json.loads(resp.get("content","[]"))
            except:
                events = []

            text, sql, citations = process_sse_response(events)

            if text:
                st.session_state.messages.append({"role":"assistant","content":text})
                st.markdown(f"**Assistant:** {text}")

                if citations:
                    st.write("Citations:")
                    for c in citations:
                        label  = c["source_id"] or "source"
                        doc_id = c["doc_id"]
                        q      = (
                            "SELECT transcript_text "
                            "FROM sales_conversations "
                            f"WHERE conversation_id = '{doc_id}'"
                        )
                        df2    = run_snowflake_query(q)
                        transcript = "No transcript available"
                        if df2 is not None:
                            pdf2 = df2.to_pandas()
                            if not pdf2.empty:
                                transcript = pdf2.iloc[0,0]
                        with st.expander(label):
                            st.write(transcript)

                handle_address_logic(query, text)

            if sql:
                st.markdown("### Generated SQL")
                st.code(sql, language="sql")
                df3 = run_snowflake_query(sql)
                if df3 is not None:
                    st.write("### Results")
                    st.dataframe(df3)

    # sidebar: reset conversation
    with st.sidebar:
        if st.button("New Conversation", key="new_chat"):
            st.session_state.messages = []
            st.rerun()


if __name__ == "__main__":
    main()
