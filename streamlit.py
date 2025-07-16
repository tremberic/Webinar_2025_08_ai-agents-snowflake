import streamlit as st
import json
import _snowflake
import pandas as pd
import re
# from geopy.geocoders import Nominatim
import numpy as np
from snowflake.snowpark.context import get_active_session
from call_here_api import call_routing_here_api, call_geocoding_here_api, decode_polyline, display_map

session = get_active_session()
# geolocator = call_geocoding_here_api(user_agent="sales_assistant")

API_ENDPOINT = "/api/v2/cortex/agent:run"
API_TIMEOUT = 50000  # in milliseconds
CORTEX_SEARCH_SERVICES = "pnp.etremblay.sales_conversation_search"
SEMANTIC_MODELS = "@pnp.etremblay.models/sales_metrics_model.yaml"


def extract_addresses(text: str) -> list[str]:
    st.write("Debug: entering extract_addresses…")
    # ── street number ── street name & type ── city/etc ── optional province/state ── optional ZIP or postal code
    pattern = (
        r"\d{1,6}\s+"  # street number
        r"[\w\.\s]+?"  # street name (incl. dots, spaces)
        r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?"
        r"|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?"
        r"|Way|Parkway|Pkwy|Court|Ct\.?|Place|Pl\.?"
        r"|Terrace|Ter\.?|Circle|Cir\.?)"  # street type
        r"|Rue|R\.?)"  # ← added Rue/R.
        r"[\w\.\s,]*?"  # rest of address (city, separators)
        r"(?:[A-Za-z]{2})?"  # optional state/province code (e.g. CA, QC)
        r"(?:\s*\d{5}(?:-\d{4})?"  # optional US ZIP (12345 or 12345‑6789)
        r"|\s*[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d)?"  # or optional Canadian postal code (A1A 1A1)
    )
    # IGNORECASE so “Ave.” or “ave” both match
    matches = re.findall(pattern, text, flags=re.IGNORECASE)
    # strip extra whitespace
    return [m.strip() for m in matches]


def geocode_address(addr: str):
    try:
        loc = call_geocoding_here_api(addr)
        return (loc.latitude, loc.longitude) if loc else (None, None)
    except Exception as e:
        st.error(f"Geocoding failed: {e}")
        return (None, None)


def handle_address_logic(query: str, response_text: str):
    combined_text = f"{query} {response_text}"
    addresses = extract_addresses(combined_text)

    # st.write("Dans handle_address_logic  {addresses}")
    st.write(f"Dans handle_address_logic: {addresses}")
    if len(addresses) >= 2:
        st.write("Dans handle_address_logic len(addresses) >= 2")
        origin = addresses[0]
        destination = addresses[1]
        lat1, lon1 = geocode_address(origin)
        lat2, lon2 = geocode_address(destination)
        if None not in (lat1, lon1, lat2, lon2):
            st.write("call_routing_here_api 1")
            here_json = call_routing_here_api((lat1, lon1), (lat2, lon2))
            coords = decode_polyline(here_json)
            display_map(coords)
        else:
            st.error("Could not geocode one or both addresses.")
    elif len(addresses) == 1:
        st.write("call geocode_address11")
        lat, lon = geocode_address(addresses[0])
        if lat is not None and lon is not None:
            st.write("DRAW MAP")
            st.write(f"Map for: {addresses[0]}")
            st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}))


def run_snowflake_query(query):
    try:
        df = session.sql(query.replace(';', ''))
        return df
    except Exception as e:
        st.error(f"Error executing SQL: {str(e)}")
        return None, None


def snowflake_api_call(query: str, limit: int = 10):
    payload = {
        "model": "claude-4-sonnet",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": query}]
            }
        ],
        "tools": [
            {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "analyst1"}},
            {"tool_spec": {"type": "cortex_search", "name": "search1"}},
            {"tool_spec": {"type": "http_request", "name": "here_maps"}}
        ],
        "tool_resources": {
            "analyst1": {"semantic_model_file": SEMANTIC_MODELS},
            "search1": {
                "name": CORTEX_SEARCH_SERVICES,
                "max_results": limit,
                "id_column": "conversation_id"
            }
        }
    }

    try:
        resp = _snowflake.send_snow_api_request(
            "POST", API_ENDPOINT, {}, {}, payload, None, API_TIMEOUT
        )
        if resp["status"] != 200:
            st.error(f"❌ HTTP Error: {resp['status']} - {resp.get('reason', 'Unknown reason')}")
            st.error(f"Response details: {resp}")
            return None
        return json.loads(resp["content"])
    except Exception as e:
        st.error(f"Error making request: {str(e)}")
        return None


def process_sse_response(response):
    text = ""
    sql = ""
    citations = []
    if not response or isinstance(response, str):
        return text, sql, citations
    try:
        for event in response:
            if event.get('event') == "message.delta":
                print("ALLOsss34")
                data = event.get('data', {})
                delta = data.get('delta', {})
                for content_item in delta.get('content', []):
                    content_type = content_item.get('type')
                    if content_type == "tool_results":
                        tool_results = content_item.get('tool_results', {})
                        if 'content' in tool_results:
                            for result in tool_results['content']:
                                if result.get('type') == 'json':
                                    text += result.get('json', {}).get('text', '')
                                    sql = result.get('json', {}).get('sql', '')
                                    for sr in result.get('json', {}).get('searchResults', []):
                                        citations.append({
                                            'source_id': sr.get('source_id', ''),
                                            'doc_id': sr.get('doc_id', '')
                                        })
                    elif content_type == 'text':
                        text += content_item.get('text', '')
    except Exception as e:
        st.error(f"Error processing events: {str(e)}")
    return text, sql, citations


def main():
    st.title("Webinar Intelligent Sales Assistant")

    with st.sidebar:
        if st.button("New Conversation", key="new_chat"):
            st.session_state.messages = []
            st.rerun()

    if 'messages' not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message['role']):
            st.markdown(message['content'].replace("•", "\n\n"))

    if query := st.chat_input("Would you like to learn?"):
        with st.chat_message("user"):

            st.markdown(query)
        st.session_state.messages.append({"role": "user", "content": query})

        with st.spinner("Processing your request..."):
            response = snowflake_api_call(query, 1)
            text, sql, citations = process_sse_response(response)

        if text:

            text = text.replace("【†", "[").replace("†】", "]")
            st.session_state.messages.append({"role": "assistant", "content": text})
            with st.chat_message("assistant"):
                st.markdown(text.replace("•", "\n\n"))
            st.write("ALLO3")
            if citations:
                st.write("Citations:")
                for citation in citations:
                    doc_id = citation.get("doc_id", "")
                    if doc_id:
                        query = f"SELECT transcript_text FROM sales_conversations WHERE conversation_id = '{doc_id}'"
                        result = run_snowflake_query(query)
                        result_df = result.to_pandas()
                        transcript_text = result_df.iloc[0, 0] if not result_df.empty else "No transcript available"
                        with st.expander(f"[{citation.get('source_id', '')}]"):
                            st.write(transcript_text)

            # NEW: Automatically handle address logic
            st.write("CALL handle_address_logic(query")
            handle_address_logic(query, text)

        if sql:
            st.markdown("### Generated SQL")
            st.code(sql, language="sql")
            sales_results = run_snowflake_query(sql)
            if sales_results:
                st.write("### Sales Metrics Report")
                st.dataframe(sales_results)


if __name__ == "__main__":
    main()
