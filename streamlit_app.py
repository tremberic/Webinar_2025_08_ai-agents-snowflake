#Maintenant sync dans GIT
import streamlit as st
import json
import _snowflake
import pandas as pd
import re
import numpy as np
from snowflake.snowpark.context import get_active_session
from call_here_api import (
    call_routing_here_api,
    call_geocoding_here_api,
    decode_polyline,
    display_map,
    call_routing_here_api_v7,
    decode_shape
)

session = get_active_session()

API_ENDPOINT           = "/api/v2/cortex/agent:run"
API_TIMEOUT            = 50_000    # milliseconds
CORTEX_SEARCH_SERVICES = "pnp.etremblay.sales_conversation_search"
SEMANTIC_MODELS        = "@pnp.etremblay.models/sales_metrics_model.yaml"
CORTEX_MODEL           = "claude-4-sonnet"


def process_sse_response(events):
    text = ""
    sql = ""
    citations = []
    for event in events:
        if event.get("event") == "message.delta":
            for c in event["data"]["delta"].get("content", []):
                if c["type"] == "text":
                    text += c["text"]
                elif c["type"] == "tool_results":
                    for result in c["tool_results"]["content"]:
                        if result["type"] == "json":
                            j = result["json"]
                            text += j.get("text", "")
                            sql = j.get("sql", sql)
                            for sr in j.get("searchResults", []):
                                citations.append({
                                    "source_id": sr.get("source_id", ""),
                                    "doc_id":    sr.get("doc_id", ""),
                                })
    return text, sql, citations


def extract_addresses(text: str) -> list[str]:
    st.write("🔍 Debug: asking Cortex to extract addresses…")
    prompt = (
        "Extract every full street address from this text and output **only** "
        "a JSON array of strings (no markdown, no explanation). For example:\n"
        '["123 Main St City, ST 12345", "456 Rue Example Montréal QC H2X 1Y4"]\n\n'
        f"Text:\n```{text}```"
    )
    payload = {
        "model": CORTEX_MODEL,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ],
    }
    resp = _snowflake.send_snow_api_request(
        "POST", API_ENDPOINT, {}, {}, payload, None, API_TIMEOUT
    )
    status      = resp.get("status")
    content_str = resp.get("content", "")

    st.write("❗ RAW response status:", status)
    st.write("❗ RAW response content:", repr(content_str))

    if status != 200:
        st.error(f"Agent error {status}")
        return []

    try:
        events = json.loads(content_str)
    except json.JSONDecodeError as e:
        st.error(f"Failed to parse SSE JSON: {e}")
        return []

    full_text, _, _ = process_sse_response(events)
    st.write("🔍 Debug — raw agent reply:", full_text)

    cleaned = re.sub(r"```(?:json)?", "", full_text, flags=re.IGNORECASE).strip()
    st.write("🔍 Debug — cleaned reply:", cleaned)

    m = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if not m:
        st.error("No JSON array found in agent output.")
        return []

    try:
        addresses = json.loads(m.group(0))
        st.write("🔍 Debug — parsed addresses:", addresses)
        return addresses
    except json.JSONDecodeError as e:
        st.error(f"Failed to decode JSON array: {e}")
        return []


def geocode_address(addr: str):
    try:
        geo = call_geocoding_here_api(addr)
        items = geo.get("items") or []
        if not items:
            return None, None
        pos = items[0]["position"]
        lat, lon = pos["lat"], pos["lng"]
        st.write(f"🔍 Geocoded '{addr}' → lat: {lat}, lon: {lon}")
        return lat, lon
    except Exception as e:
        st.error(f"Geocoding failed for '{addr}': {e}")
        return None, None


def handle_address_logic(query: str, response_text: str):
    # 1) Extract addresses from the user query
    addresses = extract_addresses(query)
    st.write("🔍 extracted addresses:", addresses)

    # 2) Fallback to “between X and Y”
    if not addresses:
        m = re.search(r"between\s+(.*?)\s+and\s+(.*)", query, flags=re.IGNORECASE)
        if m:
            addresses = [m.group(1).strip(" ,."), m.group(2).strip(" ,.")]
            st.write("🔍 fallback addresses:", addresses)

    # 3) Single address → just geocode + st.map
    if len(addresses) == 1:
        addr = addresses[0]
        try:
            geo = call_geocoding_here_api(addr)
            items = geo.get("items") or []
            if not items:
                st.error(f"No geocoding result for '{addr}'")
                return
            pos = items[0]["position"]
            lat, lon = pos["lat"], pos["lng"]
            st.write(f"📍 Map for: {addr}")
            # use st.map for a single point
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}))
        except Exception as e:
            st.error(f"Geocoding failed for '{addr}': {e}")
        return

    # 4) Two addresses → full route via Snowflake proc + display_map
    if len(addresses) == 2:
        origin, destination = addresses

        lat1, lon1 = geocode_address(origin)
        lat2, lon2 = geocode_address(destination)
        if None in (lat1, lon1, lat2, lon2):
            st.error("Could not geocode one or both addresses.")
            return

        here_v7 = call_routing_here_api_v7((lat1, lon1), (lat2, lon2))
        coords  = decode_shape(here_v7)
        display_map(coords)
        return

    # 5) otherwise nothing to do
    st.write("ℹ️ No address(es) found to map.")




def run_snowflake_query(query):
    try:
        return session.sql(query.replace(";", ""))
    except Exception as e:
        st.error(f"Error executing SQL: {e}")
        return None


def snowflake_api_call(query: str, limit: int = 10):
    payload = {
        "model": "claude-4-sonnet",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": query}]}
        ],
        "tools": [
            {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "analyst1"}},
            {"tool_spec": {"type": "cortex_search",               "name": "search1"}},
            {"tool_spec": {"type": "http_request",               "name": "here_maps"}}
        ],
        "tool_resources": {
            "analyst1": {"semantic_model_file": SEMANTIC_MODELS},
            "search1": {
                "name": CORTEX_SEARCH_SERVICES,
                "max_results": limit,
                "id_column": "conversation_id",
            }
        }
    }
    try:
        resp = _snowflake.send_snow_api_request(
            "POST", API_ENDPOINT, {}, {}, payload, None, API_TIMEOUT
        )
        if resp["status"] != 200:
            st.error(f"❌ HTTP Error: {resp['status']}")
            return None
        return json.loads(resp["content"])
    except Exception as e:
        st.error(f"Error making request: {e}")
        return None


def main():
    st.title("Webinar Intelligent Sales Assistant")

    with st.sidebar:
        if st.button("New Conversation"):
            st.session_state.messages = []
            st.rerun()

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"].replace("•", "\n\n"))

    if query := st.chat_input("Would you like to learn?"):
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        with st.spinner("Processing your request..."):
            response = snowflake_api_call(query, 1)
            text, sql, citations = process_sse_response(response or [])

        if text:
            st.session_state.messages.append({"role": "assistant", "content": text})
            with st.chat_message("assistant"):
                st.markdown(text.replace("•", "\n\n"))

            if citations:
                st.write("Citations:")
                for c in citations:
                    label = str(c.get("source_id", "")) or "source"
                    q = (
                        "SELECT transcript_text "
                        "FROM sales_conversations "
                        f"WHERE conversation_id = '{c.get('doc_id','')}'"
                    )
                    df = run_snowflake_query(q)
                    txt = "No transcript available"
                    if df is not None:
                        pdf = df.to_pandas()
                        if not pdf.empty:
                            txt = pdf.iloc[0, 0]
                    with st.expander(label):
                        st.write(txt)

            handle_address_logic(query, text)

        if sql:
            st.markdown("### Generated SQL")
            st.code(sql, language="sql")
            df = run_snowflake_query(sql)
            if df is not None:
                st.write("### Sales Metrics Report")
                st.dataframe(df)


if __name__ == "__main__":
    main()
