# bin_request_retrieval.py

import json
from snowflake.snowpark.context import get_active_session

session = get_active_session()

def fetch_bin_requests() -> list[dict]:
    """
    Fetch unread emails, call Cortex.COMPLETE, unwrap the envelope
    under choices[0].messages, and return:
      - message_id
      - raw_body
      - json_output (the *inner* JSON string)
      - container_format, quantity, date_needed, requester
    """
    REQUEST_SQL = """
    SELECT
      message_id,
      body AS raw_body,
      SNOWFLAKE.CORTEX.COMPLETE(
        'claude-4-sonnet',
        [
          {'role':'system',
           'content': $$Extract a JSON object with exactly these keys:
             "container_format","quantity","date_needed","requester".
             Output only the JSON object (no markdown).$$},
          {'role':'user', 'content': body}
        ],
        {}
      ) AS full_response
    FROM emails_webinar_202508
    WHERE is_read = FALSE
    LIMIT 5
    """
    df  = session.sql(REQUEST_SQL)
    pdf = df.to_pandas()

    results = []
    for row in pdf.itertuples(index=False):
        raw     = row.RAW_BODY or ""
        outer_j = row.FULL_RESPONSE or "{}"

        # 1) parse the outer envelope
        try:
            outer = json.loads(outer_j)
        except json.JSONDecodeError:
            outer = {}

        # 2) drill into choices[0].messages
        choices = outer.get("choices", [])
        if choices and isinstance(choices, list):
            msg_str = choices[0].get("messages", "")
        else:
            msg_str = ""

        # 3) parse that inner JSON
        try:
            inner = json.loads(msg_str)
        except json.JSONDecodeError:
            inner = {}

        # 4) pull out your four keys (default to empty string)
        fmt = inner.get("container_format", "")
        qty = inner.get("quantity", "")
        dt  = inner.get("date_needed", "")
        req = inner.get("requester", "")

        results.append({
            "message_id":       row.MESSAGE_ID,
            "raw_body":         raw,
            "json_output":      msg_str,    # the *inner* JSON
            "container_format": fmt,
            "quantity":         qty,
            "date_needed":      dt,
            "requester":        req,
        })

    return results

def mark_request_read(message_id: str) -> None:
    session.sql(f"""
      UPDATE emails_webinar_202508
      SET is_read = TRUE
      WHERE message_id = '{message_id}'
    """).collect()
