import requests
import pandas as pd
import pydeck as pdk
import streamlit as st
import polyline  # install via `pip install polyline`

HERE_API_KEY = "I6NclWcjeFKXl57Q_IwyajkiXXr2QZg9vb49IZsl80E"

# You can call call_here_api from your agent logic when the user requests map data.
# After parsing the HERE response with decode_polyline, pass the resulting coordinates
# to display_map to render them in Streamlit.

def call_here_api(origin, destination):
    params = {
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "return": "polyline",
        "apikey": HERE_API_KEY,
    }
    resp = requests.get("https://router.hereapi.com/v8/routes", params=params)
    resp.raise_for_status()
    return resp.json()

def decode_polyline(here_json):
    coords = []
    for route in here_json.get("routes", []):
        for section in route.get("sections", []):
            poly = section.get("polyline")
            if poly:
                coords.extend(polyline.decode(poly))
    return coords

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
