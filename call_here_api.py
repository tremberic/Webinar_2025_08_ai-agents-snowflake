# call_here_api.py
import streamlit as st
import os
import _snowflake
import requests
#import flexpolyline
from typing import Tuple, Dict, List, Union

# import flexpolyline  # pip install flexpolyline

secret = _snowflake.get_generic_secret_string("here_api_key")
os.environ["HERE_API_KEY"] = secret


def call_geocoding_here_api(address: str) -> Dict:
    params = {
        "q": address,
        "apiKey": secret,  # camelCase for Geocoding API
    }
    resp = requests.get(
        "https://geocode.search.hereapi.com/v1/geocode",
        params=params,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def call_routing_here_api(
    origin: Tuple[float, float],
    destination: Tuple[float, float],
) -> Tuple[Dict, dict]:
    # 1) Proof we entered the function
    #print("⚠️ call_routing_here_api() was called")
    #raise RuntimeError("forced crash so we know we ran it")

    # 2) (Unreachable for now) your real logic:
    params = {
        "transportMode": "car",
        "origin":        f"{origin[0]},{origin[1]}",
        "destination":   f"{destination[0]},{destination[1]}",
        "return":        "polyline",
        "apikey":        secret,
    }
    st.write(f"🔍 Debug — Dans call_routing_here_api: {params}")
    resp = requests.get(
        "https://router.hereapi.com/v8/routes",
        params=params,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json(), params


def call_routing_here_api_v7(
        origin: Tuple[float, float],
        destination: Tuple[float, float]
) -> Dict:
    """
    Calls HERE Routing v7 to get a route with an unencoded 'shape' array.
    """
    url = "https://route.ls.hereapi.com/routing/7.2/calculateroute.json"
    params = {
        "apiKey": secret,
        "waypoint0": f"geo!{origin[0]},{origin[1]}",
        "waypoint1": f"geo!{destination[0]},{destination[1]}",
        "mode": "fastest;car;traffic:disabled",
        "representation": "display",  # ← returns 'shape' instead of polyline
        "legAttributes": "shape"  # ← include the raw coordinate list
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()

def decode_shape(response: dict) -> list[tuple[float, float]]:
    """
    Given the JSON from the v7 HERE API, pull out
    response → route[0] → leg[0] → shape,
    which is an array of "lat,lon" strings,
    and return [(lat, lon), …].
    """
    # Drill into the JSON to get the array of "lat,lon" strings
    shape = response["response"]["route"][0]["leg"][0]["shape"]
    # Split each "lat,lon" and convert to floats
    coords = [
        (float(lat), float(lon))
        for lat, lon in (point.split(",") for point in shape)
    ]
    return coords

def decode_polyline(data: Union[str, Dict]) -> List[Tuple[float, float]]:
    """
    Decode either:
      - a HERE JSON response (dict) by recursing into routes→sections→polyline
      - or a flexible‑polyline string by calling flexpolyline.decode()
    """
    if isinstance(data, dict):
        coords: List[Tuple[float, float]] = []
        for route in data.get("routes", []):
            for section in route.get("sections", []):
                poly = section.get("polyline")
                if poly:
                    coords.extend(decode_polyline(poly))
        return coords

    # otherwise it's a flexible‑polyline string
    return flexpolyline.decode(data)


def display_map(coords: List[Tuple[float, float]]):
    import pandas as pd
#    import pydeck as pdk
    import streamlit as st

    if not coords:
        st.write("No coordinates to display.")
        return

    df = pd.DataFrame(coords, columns=["lat", "lon"])
    layer = pdk.Layer(
        "PathLayer",
        data=df,
        get_path="[[lon, lat] for lon, lat in zip(df['lon'], df['lat'])]",
        get_width=5,
        pickable=False
    )
    view_state = pdk.ViewState(
        latitude=df["lat"].mean(),
        longitude=df["lon"].mean(),
        zoom=10
    )
    st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view_state))
