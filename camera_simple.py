import cv2
import requests
import time
import logging # Let’s replace it with `print` or set up basic logging.
import base64
import hashlib
import random # Replaced numpy.random
from datetime import datetime, timezone
from enum import Enum

# --- Configuration Start ---
RTSP_URL = "rtsp://admin:123456@192.168.10.14:554"  # Replace with your URL
ONVIF_HOST = "192.168.0.167"  # Camera IP address for ONVIF control
ONVIF_USER = "admin"  # Username for ONVIF
ONVIF_PASSWORD = "123456"  # Password for ONVIF (IMPORTANT: replace with your own!)
# Possible values: "YOOSEE", "YCC365", "Y05"
SELECTED_CAMERA_TYPE_NAME = "YCC365"
PTZ_MOVE_TIME = 0.4  # Camera movement duration per single key press (in seconds)
IS_INVERT_UPDOWN_PTZ = False # Whether to invert up/down controls

# Movement speeds (from -1.0 to 1.0)
PAN_SPEED = 0.5
TILT_SPEED = 0.5
ZOOM_SPEED = 0.5 # For cameras that support zoom speed control via ContinuousMove
# --- End of Configuration ---

# Setting up basic logging (can be replaced with print)
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)
def logger_error(msg): print(f"ERROR: {msg}")
def logger_info(msg): print(f"INFO: {msg}")


class CameraType(Enum):
    YOOSEE = "YOOSEE"
    YCC365 = "YCC365"
    Y05 = "Y05"

try:
    SELECTED_CAMERA_TYPE = CameraType[SELECTED_CAMERA_TYPE_NAME]
except KeyError:
    logger_error(f"Invalid camera type: {SELECTED_CAMERA_TYPE_NAME}. Available: {', '.join([t.name for t in CameraType])}")
    exit()

# Constants from the original script (adapted)
Y05_DEFAULT_PROFILE = "PROFILE_000"
Y05_DEFAULT_PRESET_TOKEN = "Preset1" # Пример
Y05_SERVICE_PATH = ":6688/onvif/ptz_service"

YOOSEE_DEFAULT_PROFILE = "IPCProfilesToken1"
YOOSEE_SERVICE_PATH = ":5000/onvif/deviceio_service" # The original used deviceio_service, but for PTZ, ptz_service is typically used.
# Verify the correct service path for your YOOSEE camera's PTZ functionality. It is often /onvif/ptz_service.
# If deviceio_service is used, the commands may differ.
# For YOOSEE PTZ, the path might be the same as for the Y05 model, or it might use a different port.
# For better compatibility, PTZ is usually located at /onvif/ptz_service or a similar path.
# Let's assume the YOOSEE PTZ might be at the standard PTZ path if deviceio does not work for PTZ.
# YOOSEE_PTZ_SERVICE_PATH = ":5000/onvif/ptz_service" # Example of an alternative path

YCC365_DEFAULT_PROFILE = "Profile_1"
YCC365_SERVICE_PATH = "/onvif/PTZ" # For YCC365
YCC365_DEFAULT_HEADERS = {'Content-Type': 'application/soap+xml;charset=UTF8'}


# --- Functions for ONVIF PTZ control (adapted) ---

def get_camera_onvif_params(camera_type_enum):
    """Returns the service profile and path for a camera type"""
    if camera_type_enum == CameraType.Y05:
        return Y05_DEFAULT_PROFILE, Y05_SERVICE_PATH
    elif camera_type_enum == CameraType.YOOSEE:
        # Important: ensure that YOOSEE_SERVICE_PATH is correct for PTZ commands
        return YOOSEE_DEFAULT_PROFILE, YOOSEE_SERVICE_PATH # or YOOSEE_PTZ_SERVICE_PATH
    elif camera_type_enum == CameraType.YCC365:
        return YCC365_DEFAULT_PROFILE, YCC365_SERVICE_PATH
    return None, None

def generate_onvif_wssecurity_header(user, password):
    """Generates a WS-Security SOAP header with PasswordDigest"""
    creation_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z" # Format with milliseconds
    nonce_str = str(random.randint(100000000, 999999999)) # Longer nonce
    nonce_base64 = base64.b64encode(nonce_str.encode('utf-8')).decode('ascii')

    # Digest = Base64(SHA1(Nonce + Created + Password))
    # The original script for YOOSEE used a HEX digest (b16encode).
    # This is non-standard for a PasswordDigest, but since it worked, it must be replicated.
    # A standard PasswordDigest is base64(sha1(nonce_bytes + created_bytes + password_bytes)).

    # For YOOSEE/Y05, if the original script used HEX (b16encode):
    # sha1_hash = hashlib.sha1(nonce_str.encode('utf-8') + creation_date.encode('utf-8') + password.encode('utf-8')).digest()
    # password_digest_final = base64.b16encode(sha1_hash).decode('ascii')

    # Using the standard PasswordDigest (Base64).
    # For this, the Nonce must be in byte format—not as a Base64 string—when calculating the hash.
    nonce_bytes = nonce_str.encode('utf-8') # Use the nonce_str itself, rather than its base64 representation, in the hash
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

def build_onvif_envelope(user, password, service_content, camera_type_enum_for_auth):
    """Собирает полный SOAP конверт"""
    header_part = ""
    # YOOSEE and Y05 use the WS-Security header from the original script
    if camera_type_enum_for_auth in [CameraType.YOOSEE, CameraType.Y05]:
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

def ptz_stop_request(host, camera_type_enum, user, password):
    """Sends a Stop command"""
    profile_token, service_path_suffix = get_camera_onvif_params(camera_type_enum)
    if not profile_token:
        logger_error(f"Failed to get parameters for {camera_type_enum.name}")
        return

    service_url = f"http://{host}{service_path_suffix}"
    xml_payload = ""
    headers = {}

    if camera_type_enum in [CameraType.YOOSEE, CameraType.Y05]:
        service_content = f"""<Stop xmlns="http://www.onvif.org/ver20/ptz/wsdl">
                                <ProfileToken>{profile_token}</ProfileToken>
                                <PanTilt>true</PanTilt>
                                <Zoom>true</Zoom>
                            </Stop>"""
        xml_payload = build_onvif_envelope(user, password, service_content, camera_type_enum)
        action_url = "http://www.onvif.org/ver20/ptz/wsdl/Stop" # Intended action
        headers = {'Content-Type': f'application/soap+xml;charset=UTF8;action="{action_url}"'}

    elif camera_type_enum == CameraType.YCC365:
        xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
                        <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl">
                            <soap:Body>
                                <tptz:Stop>
                                    <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
                                    <tptz:PanTilt>true</tptz:PanTilt>
                                    <tptz:Zoom>true</tptz:Zoom>
                                </tptz:Stop>
                            </soap:Body>
                        </soap:Envelope>"""
        headers = YCC365_DEFAULT_HEADERS
        service_url = f"http://{host}{YCC365_SERVICE_PATH}" # YCC365 has its own service_url

    if xml_payload:
        try:
            logger_info(f"STOP Request to {service_url} for {camera_type_enum.name}")
            # logger_info(f"Payload: {xml_payload}") # Для отладки
            r = requests.post(service_url, data=xml_payload, headers=headers, timeout=2)
            logger_info(f"STOP Response: {r.status_code}")
            if r.status_code != 200 and r.status_code != 204 : # Некоторые камеры отвечают 204 No Content
                 logger_error(f"PTZ Stop Error: {r.status_code} - {r.text}")
        except requests.exceptions.RequestException as e:
            logger_error(f"PTZ Stop Exception: {e}")

def ptz_continuous_move_request(host, camera_type_enum, user, password, x, y, z):
    """Отправляет команду ContinuousMove"""
    profile_token, service_path_suffix = get_camera_onvif_params(camera_type_enum)
    if not profile_token:
        logger_error(f"Не удалось получить параметры для {camera_type_enum.name}")
        return

    service_url = f"http://{host}{service_path_suffix}"
    xml_payload = ""
    headers = {}

    if camera_type_enum in [CameraType.YOOSEE, CameraType.Y05]:
        # Убедимся, что пространство имен для PanTilt и Zoom соответствует схеме ONVIF ver10
        service_content = f"""<ContinuousMove xmlns="http://www.onvif.org/ver20/ptz/wsdl">
                                <ProfileToken>{profile_token}</ProfileToken>
                                <Velocity>
                                    <PanTilt x="{x}" y="{y}" space="http://www.onvif.org/ver10/tptz/PanTiltSpaces/VelocityGenericSpace" xmlns="http://www.onvif.org/ver10/schema"/>
                                    <Zoom x="{z}" space="http://www.onvif.org/ver10/tptz/ZoomSpaces/VelocityGenericSpace" xmlns="http://www.onvif.org/ver10/schema"/>
                                </Velocity>
                            </ContinuousMove>"""
        xml_payload = build_onvif_envelope(user, password, service_content, camera_type_enum)
        action_url = "http://www.onvif.org/ver20/ptz/wsdl/ContinuousMove" # Предполагаемое действие
        headers = {'Content-Type': f'application/soap+xml;charset=UTF8;action="{action_url}"'}

    elif camera_type_enum == CameraType.YCC365:
        xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
                        <soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
                            <soap:Body>
                                <tptz:ContinuousMove>
                                    <tptz:ProfileToken>{profile_token}</tptz:ProfileToken>
                                    <tptz:Velocity>
                                        <tt:PanTilt x="{x}" y="{y}"/>
                                        <tt:Zoom x="{z}"/>
                                    </tptz:Velocity>
                                </tptz:ContinuousMove>
                            </soap:Body>
                        </soap:Envelope>"""
        headers = YCC365_DEFAULT_HEADERS
        service_url = f"http://{host}{YCC365_SERVICE_PATH}"


    if xml_payload:
        try:
            logger_info(f"MOVE Request ({x},{y},{z}) to {service_url} for {camera_type_enum.name}")
            # logger_info(f"Payload: {xml_payload}") # For debugging
            r = requests.post(service_url, data=xml_payload.encode('utf-8'), headers=headers, timeout=2) # encode to utf-8
            logger_info(f"MOVE Response: {r.status_code}")
            if r.status_code != 200:
                 logger_error(f"PTZ Move Error: {r.status_code} - {r.text}")
        except requests.exceptions.RequestException as e:
            logger_error(f"PTZ Move Exception: {e}")

# --- Main application with OpenCV ---
if __name__ == "__main__":
    logger_info(f"Connecting to an RTSP stream: {RTSP_URL}")
    cap = cv2.VideoCapture(RTSP_URL)

    if not cap.isOpened():
        logger_error(f"Error: Failed to connect to the RTSP stream at the address: {RTSP_URL}")
        exit()

    logger_info("RTSP stream opened. Camera controls:")
    logger_info("  w: Up, s: Down, a: Left, d: Right")
    logger_info("  z: Zoom In, x: Zoom Out")
    logger_info("  Space: Stop")
    logger_info("  q: Exit")

    window_name = 'RTSP Stream with PTZ Control'
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    last_move_time = 0
    is_moving = False

    while True:
        ret, frame = cap.read()
        if not ret:
            logger_error("Error: Failed to retrieve a frame. The stream has ended or been lost.")
            break

        cv2.imshow(window_name, frame)
        key = cv2.waitKey(1) & 0xFF # Wait 1ms for smoother video

        current_time = time.time()

        # Automatic movement stop after PTZ_MOVE_TIME
        if is_moving and (current_time - last_move_time > PTZ_MOVE_TIME):
            logger_info("Auto-stopping PTZ movement.")
            ptz_stop_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD)
            is_moving = False

        if key == ord('q'):
            if is_moving: # Stop moving before the exit
                 ptz_stop_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD)
            logger_info("Выход...")
            break

        # Movement commands
        # Up/down movement is inverted if IS_INVERT_UPDOWN_PTZ = True
        y_up = -TILT_SPEED if IS_INVERT_UPDOWN_PTZ else TILT_SPEED
        y_down = TILT_SPEED if IS_INVERT_UPDOWN_PTZ else -TILT_SPEED

        if key == ord('w'): # Вверх
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, 0, y_up, 0)
            last_move_time = current_time
            is_moving = True
        elif key == ord('s'): # Down
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, 0, y_down, 0)
            last_move_time = current_time
            is_moving = True
        elif key == ord('a'): # Left
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, -PAN_SPEED, 0, 0)
            last_move_time = current_time
            is_moving = True
        elif key == ord('d'): # Right
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, PAN_SPEED, 0, 0)
            last_move_time = current_time
            is_moving = True
        elif key == ord('z'): # Zoom In
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, 0, 0, ZOOM_SPEED)
            last_move_time = current_time
            is_moving = True
        elif key == ord('x'): # Zoom Out
            ptz_continuous_move_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD, 0, 0, -ZOOM_SPEED)
            last_move_time = current_time
            is_moving = True
        elif key == ord(' '): # Stop
            ptz_stop_request(ONVIF_HOST, SELECTED_CAMERA_TYPE, ONVIF_USER, ONVIF_PASSWORD)
            is_moving = False
        # Add other commands, for example for presets or a home position, if needed.

    cap.release()
    cv2.destroyAllWindows()
    logger_info("The program is complete.")