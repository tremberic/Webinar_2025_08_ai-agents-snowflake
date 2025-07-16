import requests
import pandas as pd
import pydeck as pdk
import streamlit as st

HERE_API_KEY = "I6NclWcjeFKXl57Q_IwyajkiXXr2QZg9vb49IZsl80E"


# You can call call_here_api from your agent logic when the user requests map data.
# After parsing the HERE response with decode_polyline, pass the resulting coordinates
# to display_map to render them in Streamlit.

def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    index = 0
    lat = 0
    lng = 0
    coords: list[tuple[float, float]] = []

    while index < len(encoded):
        # decode latitude
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        # decode longitude
        result = 0
        shift = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append((lat / 1e5, lng / 1e5))

    return coords


####Called by streamlit_app
def call_routing_here_api(origin, destination):
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "return": "polyline",
        "apikey": HERE_API_KEY,
    }

    st.write("call_routing_here_api")
    resp = requests.get("https://router.hereapi.com/v8/routes", params=params)
    resp.raise_for_status()
    return resp.json()


def call_geocoding_here_api(address):
    params = {
        "q": address,
        "apikey": HERE_API_KEY,
    }
    st.write("call_geocoding_here_api")
    resp = requests.get("https://discover.search.hereapi.com/v1/geocode", params=params)
    resp.raise_for_status()
    return resp.json()


####Called by streamlit_app
def decode_polyline(here_json):
    coords = []
    for route in here_json.get("routes", []):
        for section in route.get("sections", []):
            poly = section.get("polyline")
            if poly:
                coords.extend(decode_polyline(poly))
    return coords


####Called by streamlit_app
def display_map(coords):
    if not coords:
        st.write("No coordinates found.")
        return
    df = pd.DataFrame(coords, columns=["lat", "lon"])
    layer = pdk.Layer(
        "ScatterplotLayer",
        data=df,
        get_position="[lon, lat]",
        get_color="[0, 200, 0]",
        get_radius=80,
    )
    view_state = pdk.ViewState(
        latitude=df["lat"].mean(),
        longitude=df["lon"].mean(),
        zoom=10,
    )
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
