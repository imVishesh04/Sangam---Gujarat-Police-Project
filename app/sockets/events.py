"""
SocketIO Event Handlers

Namespaces
----------
/alerts         — real-time alert pushes when a watchlist match is found
                  Event: "new_alert"  payload: enriched Alert dict

/cameras        — real-time camera online/offline status changes
                  Event: "status_change"  payload: { camera_id, status, last_seen }

Clients subscribe to whichever namespace they need.
The matching engine emits to /alerts.
The video ingestion worker emits to /cameras when a stream goes online or offline.
"""
from app import socketio


# ---------------------------------------------------------------------------
# /alerts namespace
# ---------------------------------------------------------------------------

@socketio.on("connect", namespace="/alerts")
def alerts_connect():
    """Client connected to the alerts channel."""
    pass


@socketio.on("disconnect", namespace="/alerts")
def alerts_disconnect():
    pass


# ---------------------------------------------------------------------------
# /cameras namespace
# ---------------------------------------------------------------------------

@socketio.on("connect", namespace="/cameras")
def cameras_connect():
    """
    Client connected to the camera status channel.
    On connect, emit the current snapshot of all camera statuses so the
    frontend map can initialise without waiting for the next change event.
    """
    try:
        from flask_socketio import emit
        from app.services.video_ingestion import get_all_statuses
        # emit() inside a connect handler replies to the connecting client only
        # (socketio.emit broadcast the snapshot to every connected client).
        emit("status_snapshot", get_all_statuses())
    except Exception:
        pass


@socketio.on("disconnect", namespace="/cameras")
def cameras_disconnect():
    pass
