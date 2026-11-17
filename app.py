from flask import Flask, Response, request, jsonify, render_template # render_template has been added
from flask_cors import CORS
import cv2
import requests
import time
import base64
import hashlib
import random
from datetime import datetime, timezone
from enum import Enum
import threading # For background video capture

# --- Initial Configuration (can be overridden by requests) ---
DEFAULT_RTSP_URL = "rtsp://admin:123456@192.168.0.167:554"
# --- End of Configuration ---

app = Flask(__name__) # Flask automatically looks for 'static' and 'templates' in the same directory
CORS(app)

# --- Logging ---
def logger_error(msg): print(f"ERROR: {msg}")
def logger_info(msg): print(f"INFO: {msg}")

# --- ONVIF code from the original script ---
class CameraType(Enum):
    YOOSEE = "YOOSEE"
    YCC365 = "YCC365"
    Y05 = "Y05"

# Constants for ONVIF
Y05_DEFAULT_PROFILE = "PROFILE_000"
Y05_SERVICE_PATH = ":6688/onvif/ptz_service"

YOOSEE_DEFAULT_PROFILE = "IPCProfilesToken1"
YOOSEE_SERVICE_PATH = ":5000/onvif/ptz_service"

YCC365_DEFAULT_PROFILE = "Profile_1"
YCC365_SERVICE_PATH = "/onvif/PTZ"
YCC365_DEFAULT_HEADERS = {'Content-Type': 'application/soap+xml;charset=UTF8'}


def get_camera_onvif_params(camera_type_enum_str):
    try:
        camera_type_enum = CameraType[camera_type_enum_str.upper()]
    except KeyError:
        logger_error(f"Incorrect camera type: {camera_type_enum_str}")
        return None, None

    if camera_type_enum == CameraType.Y05:
        return Y05_DEFAULT_PROFILE, Y05_SERVICE_PATH
    elif camera_type_enum == CameraType.YOOSEE:
        return YOOSEE_DEFAULT_PROFILE, YOOSEE_SERVICE_PATH
    elif camera_type_enum == CameraType.YCC365:
        return YCC365_DEFAULT_PROFILE, YCC365_SERVICE_PATH
    return None, None

def generate_onvif_wssecurity_header(user, password):
    creation_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    nonce_str = ''.join(random.choices("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=24))
    nonce_base64 = base64.b64encode(nonce_str.encode('utf-8')).decode('ascii')

    nonce_bytes = nonce_str.encode('utf-8')
    created_bytes = creation_date.encode('utf-8')
    password_bytes = password.encode('utf-8')

    sha1_hash = hashlib.sha1(nonce_bytes + created_bytes + password_bytes).digest()
    password_digest_final = base64.b64encode(sha1_hash).decode('ascii')

    security_header = f"""
    <s:Header>
        <Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
            <UsernameToken>
                <Username>{user}</Username>
                <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest_final}</Password>
                <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_base64}</Nonce>
                <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{creation_date}</Created>
            </UsernameToken>
        </Security>
    </s:Header>"""
    return security_header

def build_onvif_envelope(user, password, service_content, camera_type_enum_str):
    header_part = ""
    if camera_type_enum_str.upper() in [CameraType.YOOSEE.name, CameraType.Y05.name]:
        header_part = generate_onvif_wssecurity_header(user, password)

    return f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    {header_part}
    <s:Body>
    {service_content}
    </s:Body>
</s:Envelope>"""

def send_ptz_request(host, camera_type_str, user, password, service_content_builder, ptz_action_name, x=0, y=0, z=0):
    profile_token, service_path_suffix = get_camera_onvif_params(camera_type_str)
    if not profile_token:
        msg = f"Failed to retrieve parameters for {camera_type_str}"
        logger_error(msg)
        return jsonify({"status": "error", "message": msg}), 500

    service_url = f"http://{host}{service_path_suffix}"
    xml_payload = ""
    headers = {}

    service_content = service_content_builder(profile_token, x, y, z)

    if camera_type_str.upper() in [CameraType.YOOSEE.name, CameraType.Y05.name]:
        xml_payload = build_onvif_envelope(user, password, service_content, camera_type_str)
        onvif_action = "Stop" if ptz_action_name == "Stop" else "ContinuousMove"
        action_url = f"http://www.onvif.org/ver20/ptz/wsdl/{onvif_action}"
        headers = {'Content-Type': f'application/soap+xml;charset=UTF8;action="{action_url}"'}

    elif camera_type_str.upper() == CameraType.YCC365.name:
        xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
                        <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
                            <soap:Body>
                            {service_content}
                            </soap:Body>
                        </soap:Envelope>"""
        headers = YCC365_DEFAULT_HEADERS

    if xml_payload:
        try:
            logger_info(f"{ptz_action_name} Request to {service_url} for {camera_type_str}")
            r = requests.post(service_url, data=xml_payload.encode('utf-8'), headers=headers, timeout=3)
            logger_info(f"{ptz_action_name} Response: {r.status_code}")

            if r.status_code not in [200, 202, 204]:
                logger_error(f"PTZ {ptz_action_name} Error: {r.status_code} - {r.text}")
                return jsonify({"status": "error", "message": f"PTZ {ptz_action_name} Error: {r.status_code}", "details": r.text}), r.status_code
            return jsonify({"status": "success", "message": f"PTZ {ptz_action_name} completed: {r.status_code}", "response_text": r.text}), r.status_code
        except requests.exceptions.RequestException as e:
            logger_error(f"PTZ {ptz_action_name} Exception: {e}")
            return jsonify({"status": "error", "message": f"PTZ {ptz_action_name} Exception: {str(e)}"}), 500
    else:
        return jsonify({"status": "error", "message": "Failed to generate XML payload"}), 500

SELECTED_CAMERA_TYPE_NAME_GLOBAL = CameraType.YCC365.name

def build_stop_content(profile_token, x, y, z):
    if SELECTED_CAMERA_TYPE_NAME_GLOBAL.upper() in [CameraType.YOOSEE.name, CameraType.Y05.name]:
        return f"""<Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">
                    <ProfileToken>{profile_token}</ProfileToken>
                    <PanTilt>true</PanTilt>
                    <Zoom>true</Zoom>
                </Stop>"""
    elif SELECTED_CAMERA_TYPE_NAME_GLOBAL.upper() == CameraType.YCC365.name:
         return f"""<tptz:Stop>
                    <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
                    <tptz:PanTilt>true</tptz:PanTilt>
                    <tptz:Zoom>true</tptz:Zoom>
                </tptz:Stop>"""
    return ""

def build_continuous_move_content(profile_token, x_speed, y_speed, z_speed):
    if SELECTED_CAMERA_TYPE_NAME_GLOBAL.upper() in [CameraType.YOOSEE.name, CameraType.Y05.name]:
        return f"""<ContinuousMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">
                    <ProfileToken>{profile_token}</ProfileToken>
                    <Velocity>
                        <PanTilt x="{x_speed}" y="{y_speed}" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace" xmlns="http://www.onvif.org/ver10/schema"/>
                        <Zoom x="{z_speed}" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace" xmlns="http://www.onvif.org/ver10/schema"/>
                    </Velocity>
                </ContinuousMove>"""
    elif SELECTED_CAMERA_TYPE_NAME_GLOBAL.upper() == CameraType.YCC365.name:
        return f"""<tptz:ContinuousMove>
                    <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
                    <tptz:Velocity>
                        <tt:PanTilt x="{x_speed}" y="{y_speed}"/>
                        <tt:Zoom x="{z_speed}"/>
                    </tptz:Velocity>
                </tptz:ContinuousMove>"""
    return ""

# --- Flask Routes ---

# Route for the main HTML page
@app.route('/')
def index():
    logger_info("Request for the main page, index.html")
    return render_template('index.html')

@app.route('/api/ptz', methods=['POST'])
def ptz_control():
    global SELECTED_CAMERA_TYPE_NAME_GLOBAL
    data = request.json
    logger_info(f"PTZ request received: {data}")

    camera_ip = data.get('camera_ip')
    onvif_user = data.get('onvif_user')
    onvif_password = data.get('onvif_password')
    camera_type = data.get('camera_type')
    action = data.get('action')

    if not all([camera_ip, onvif_user, onvif_password, camera_type, action]):
        return jsonify({"status": "error", "message": "Required parameters are missing"}), 400

    SELECTED_CAMERA_TYPE_NAME_GLOBAL = camera_type

    if action == 'move':
        pan = data.get('pan', 0.0)
        tilt = data.get('tilt', 0.0)
        zoom = data.get('zoom', 0.0)
        return send_ptz_request(camera_ip, camera_type, onvif_user, onvif_password,
                                build_continuous_move_content, "ContinuousMove",
                                x=pan, y=tilt, z=zoom)
    elif action == 'stop':
        return send_ptz_request(camera_ip, camera_type, onvif_user, onvif_password,
                                build_stop_content, "Stop")
    else:
        return jsonify({"status": "error", "message": "Unknown action"}), 400

video_capture = None
video_thread = None
rtsp_url_global = DEFAULT_RTSP_URL

def capture_frames():
    global video_capture, rtsp_url_global

    # Local copy of the URL for this stream, to avoid race conditions when the URL changes
    current_capture_rtsp_url = rtsp_url_global
    logger_info(f"Capture Stream: Trying to connect to RTSP: {current_capture_rtsp_url}")

    # Create a new VideoCapture object for this thread.
    # We do not use the global `video_capture` directly for initialization here,
    # ensuring that each thread works with its own instance if necessary.
    # However, for MJPEG streaming, there is typically one source per URL.
    cap = cv2.VideoCapture(current_capture_rtsp_url)

    if not cap.isOpened():
        logger_error(f"Capture stream: Error connecting to RTSP: {current_capture_rtsp_url}")
        cap.release()
        return

    logger_info(f"Capture Stream: RTSP stream successfully opened for {current_capture_rtsp_url}")

    while True:
        # We check whether the global URL has changed; if it has, this thread should terminate
        # so that a new thread can be started for the new URL.
        if rtsp_url_global != current_capture_rtsp_url:
            logger_info(f"Capture Stream: RTSP URL has changed from {current_capture_rtsp_url} on {rtsp_url_global}. Termination of the current thread.")
            break

        if cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                logger_error(f"Capture Stream: Error getting frame with {current_capture_rtsp_url}. Поток завершен или потерян.")
                # Attempting to reconnect from within a yield generator doesn't work very well in the long run.
                # It is better to have the video_feed restart the stream.
                break

            (flag, encodedImage) = cv2.imencode(".jpg", frame)
            if not flag:
                continue
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' +
                   bytearray(encodedImage) + b'\r\n')
        else:
            logger_info(f"Capture stream: Video stream {current_capture_rtsp_url} inactive, termination.")
            break

    logger_info(f"Capture Thread: Freeing up resources for {current_capture_rtsp_url}")
    cap.release()


@app.route('/api/video-stream')
def video_feed():
    global rtsp_url_global, video_thread # video_capture is no longer managed directly here

    req_rtsp_url = request.args.get('rtsp', DEFAULT_RTSP_URL)

    # If the URL has changed, or the thread hasn't started or has finished, restart it
    # It is important to check is_alive(), as the thread might have terminated on its own (e.g., due to an error)
    if req_rtsp_url != rtsp_url_global or video_thread is None or not video_thread.is_alive():
        logger_info(f"Video feed: RTSP URL changed to: {req_rtsp_url} or the stream is not active. Restarting.")

        # If the old stream is still active (for example, if the URL has changed but the old stream hasn't yet detected this),
        # we simply update rtsp_url_global. The old stream will notice this change and terminate.
        rtsp_url_global = req_rtsp_url

        # Creating and starting a new thread
        logger_info("Video feed: Starting a new video capture stream...")
        video_thread = threading.Thread(target=capture_frames)
        video_thread.daemon = True
        video_thread.start()

        # We add a short pause to allow the stream time to start and potentially produce its first frame
        # or a connection error. This is not an ideal solution, but it can help avoid
        # sending an empty Response in cases where establishing the connection takes some time.
        time.sleep(0.5)
    else:
        logger_info("Video feed: Uses an existing active video capture stream.")

    # Return an MJPEG stream from the capture_frames function (which is now a generator)
    # capture_frames will execute within the context of video_thread
    # The Response object will call this generator to retrieve frames
    return Response(capture_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


if __name__ == '__main__':
    logger_info("Launching a Flask server for ONVIF PTZ control and video streaming...")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)

